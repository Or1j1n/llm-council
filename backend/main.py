"""FastAPI backend for LLM Council."""

import asyncio
import json
import logging
import os
from time import perf_counter
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any

from . import storage
from .council import run_full_council, generate_conversation_title, stage1_collect_responses, stage2_collect_rankings, stage3_synthesize_final, calculate_aggregate_rankings

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
VALID_UVICORN_LOG_LEVELS = {"critical", "error", "warning", "info", "debug", "trace"}
UVICORN_LOG_LEVEL = LOG_LEVEL.lower()
if UVICORN_LOG_LEVEL not in VALID_UVICORN_LOG_LEVELS:
    UVICORN_LOG_LEVEL = "info"

logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="LLM Council API")

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateConversationRequest(BaseModel):
    """Request to create a new conversation."""
    pass


class SendMessageRequest(BaseModel):
    """Request to send a message in a conversation."""
    content: str


class ConversationMetadata(BaseModel):
    """Conversation metadata for list view."""
    id: str
    created_at: str
    title: str
    message_count: int


class Conversation(BaseModel):
    """Full conversation with all messages."""
    id: str
    created_at: str
    title: str
    messages: List[Dict[str, Any]]


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "LLM Council API"}


@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations():
    """List all conversations (metadata only)."""
    return storage.list_conversations()


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(request: CreateConversationRequest):
    """Create a new conversation."""
    conversation_id = str(uuid.uuid4())
    conversation = storage.create_conversation(conversation_id)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str):
    """Get a specific conversation with all its messages."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and run the 3-stage council process.
    Returns the complete response with all stages.
    """
    request_id = str(uuid.uuid4())[:8]
    request_start = perf_counter()

    logger.info(
        "api.request.start id=%s endpoint=/api/conversations/%s/message content_chars=%d",
        request_id,
        conversation_id,
        len(request.content),
    )

    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        logger.warning(
            "api.request.not_found id=%s conversation_id=%s",
            request_id,
            conversation_id,
        )
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    # Add user message
    storage.add_user_message(conversation_id, request.content)

    # If this is the first message, generate a title
    if is_first_message:
        title_start = perf_counter()
        title = await generate_conversation_title(request.content)
        storage.update_conversation_title(conversation_id, title)
        logger.info(
            "api.request.title_generated id=%s elapsed_s=%.2f title=%s",
            request_id,
            perf_counter() - title_start,
            title,
        )

    # Run the 3-stage council process
    council_start = perf_counter()
    stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
        request.content
    )
    logger.info(
        "api.request.council_done id=%s elapsed_s=%.2f stage1=%d stage2=%d stage3_model=%s",
        request_id,
        perf_counter() - council_start,
        len(stage1_results),
        len(stage2_results),
        stage3_result.get("model", "unknown"),
    )

    # Add assistant message with all stages
    storage.add_assistant_message(
        conversation_id,
        stage1_results,
        stage2_results,
        stage3_result
    )

    # Return the complete response with metadata
    total_elapsed = perf_counter() - request_start
    logger.info(
        "api.request.done id=%s endpoint=/api/conversations/%s/message elapsed_s=%.2f",
        request_id,
        conversation_id,
        total_elapsed,
    )

    return {
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "metadata": metadata
    }


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and stream the 3-stage council process.
    Returns Server-Sent Events as each stage completes.
    """
    request_id = str(uuid.uuid4())[:8]
    request_start = perf_counter()
    logger.info(
        "api.stream.start id=%s endpoint=/api/conversations/%s/message/stream content_chars=%d",
        request_id,
        conversation_id,
        len(request.content),
    )

    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        logger.warning(
            "api.stream.not_found id=%s conversation_id=%s",
            request_id,
            conversation_id,
        )
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    async def event_generator():
        try:
            # Add user message
            storage.add_user_message(conversation_id, request.content)

            # Start title generation in parallel (don't await yet)
            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(request.content))

            # Stage 1: Collect responses
            stage_start = perf_counter()
            yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
            stage1_results = await stage1_collect_responses(request.content)
            logger.info(
                "api.stream.stage_done id=%s stage=1 elapsed_s=%.2f count=%d",
                request_id,
                perf_counter() - stage_start,
                len(stage1_results),
            )
            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"

            # Stage 2: Collect rankings
            stage_start = perf_counter()
            yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
            stage2_results, label_to_model = await stage2_collect_rankings(request.content, stage1_results)
            aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
            logger.info(
                "api.stream.stage_done id=%s stage=2 elapsed_s=%.2f count=%d",
                request_id,
                perf_counter() - stage_start,
                len(stage2_results),
            )
            yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': {'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings}})}\n\n"

            # Stage 3: Synthesize final answer
            stage_start = perf_counter()
            yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
            stage3_result = await stage3_synthesize_final(request.content, stage1_results, stage2_results)
            logger.info(
                "api.stream.stage_done id=%s stage=3 elapsed_s=%.2f model=%s",
                request_id,
                perf_counter() - stage_start,
                stage3_result.get("model", "unknown"),
            )
            yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

            # Wait for title generation if it was started
            if title_task:
                title = await title_task
                storage.update_conversation_title(conversation_id, title)
                logger.info(
                    "api.stream.title_generated id=%s title=%s",
                    request_id,
                    title,
                )
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            # Save complete assistant message
            storage.add_assistant_message(
                conversation_id,
                stage1_results,
                stage2_results,
                stage3_result
            )

            # Send completion event
            logger.info(
                "api.stream.done id=%s endpoint=/api/conversations/%s/message/stream elapsed_s=%.2f",
                request_id,
                conversation_id,
                perf_counter() - request_start,
            )
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        except Exception as e:
            logger.exception(
                "api.stream.error id=%s conversation_id=%s error=%s",
                request_id,
                conversation_id,
                str(e),
            )
            # Send error event
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level=UVICORN_LOG_LEVEL)
