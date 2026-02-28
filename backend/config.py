"""Configuration for the LLM Council."""

import os
from dotenv import load_dotenv

load_dotenv()

# OpenRouter API key
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Council members - list of OpenRouter model identifiers
COUNCIL_MODELS = [
    "qwen/qwen3-vl-235b-a22b-thinking",
    "openai/gpt-oss-120b",
    "z-ai/glm-4.5-air:free",
    "arcee-ai/trinity-large-preview:free",
]

# Chairman model - synthesizes final response
CHAIRMAN_MODEL = "qwen/qwen3-vl-235b-a22b-thinking"

# OpenRouter API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Data directory for conversation storage
DATA_DIR = "data/conversations"
