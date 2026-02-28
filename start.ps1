#!/usr/bin/env pwsh

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendDir = Join-Path $projectRoot "frontend"
$backendProcess = $null
$frontendProcess = $null

function Resolve-BackendCommand {
    $venvPython = Join-Path $projectRoot ".venv\\Scripts\\python.exe"
    if (Test-Path $venvPython) {
        return @{
            FilePath = $venvPython
            Args = @("-m", "backend.main")
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @{
            FilePath = $python.Source
            Args = @("-m", "backend.main")
        }
    }

    throw "No Python executable found. Create '.venv' or install Python and ensure it is in PATH."
}

function Resolve-FrontendCommand {
    $npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($npmCmd) {
        return @{
            FilePath = $npmCmd.Source
            Args = @("run", "dev")
        }
    }

    $cmdExe = Get-Command cmd.exe -ErrorAction SilentlyContinue
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($cmdExe -and $npm) {
        return @{
            FilePath = $cmdExe.Source
            Args = @("/c", "npm", "run", "dev")
        }
    }

    throw "'npm' was not found in a Windows-executable form. Ensure Node.js is installed and npm.cmd is available."
}

function Stop-IfRunning {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$Name
    )

    if ($null -eq $Process) {
        return
    }

    try {
        if (-not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
            Write-Host "Stopped $Name (PID $($Process.Id))."
        }
    } catch {
        Write-Warning "Failed to stop $Name cleanly: $($_.Exception.Message)"
    }
}

function Assert-PortAvailable {
    param(
        [int]$Port,
        [string]$ServiceName
    )

    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
        throw "$ServiceName cannot start: port $Port is already in use (PID $($listener.OwningProcess)). Stop the process and retry."
    }
}

Write-Host "Starting LLM Council..."
Write-Host ""

Push-Location $projectRoot
try {
    Assert-PortAvailable -Port 8001 -ServiceName "Backend"
    Assert-PortAvailable -Port 5173 -ServiceName "Frontend"

    $backendCommand = Resolve-BackendCommand
    Write-Host "Starting backend on http://localhost:8001..."
    $backendProcess = Start-Process `
        -FilePath $backendCommand.FilePath `
        -ArgumentList $backendCommand.Args `
        -WorkingDirectory $projectRoot `
        -NoNewWindow `
        -PassThru

    Start-Sleep -Seconds 2

    Write-Host "Starting frontend on http://localhost:5173..."
    $frontendCommand = Resolve-FrontendCommand
    $frontendProcess = Start-Process `
        -FilePath $frontendCommand.FilePath `
        -ArgumentList $frontendCommand.Args `
        -WorkingDirectory $frontendDir `
        -NoNewWindow `
        -PassThru

    Write-Host ""
    Write-Host "LLM Council is running."
    Write-Host "  Backend:  http://localhost:8001"
    Write-Host "  Frontend: http://localhost:5173"
    Write-Host ""
    Write-Host "Press Ctrl+C to stop both servers."

    Wait-Process -Id @($backendProcess.Id, $frontendProcess.Id)
} finally {
    Stop-IfRunning -Process $frontendProcess -Name "frontend"
    Stop-IfRunning -Process $backendProcess -Name "backend"
    Pop-Location
}
