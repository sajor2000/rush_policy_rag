# Start script for the FastAPI backend (Windows PowerShell)
# Run with: .\start_backend.ps1

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $RepoRoot ".venv"
$BackendDir = Join-Path $RepoRoot "apps\backend"

Write-Host "Starting RUSH Policy RAG Backend..." -ForegroundColor Green
Write-Host "==================================" -ForegroundColor Green

# Create virtual environment if it doesn't exist
if (-not (Test-Path $VenvDir)) {
    Write-Host "Creating virtual environment at $VenvDir..."
    python -m venv $VenvDir
}

# Activate virtual environment
$ActivateScript = Join-Path $VenvDir "Scripts\Activate.ps1"
if (-not (Test-Path $ActivateScript)) {
    Write-Host "Error: Virtual environment activation script not found at $ActivateScript" -ForegroundColor Red
    Write-Host "Try running: python -m venv $VenvDir" -ForegroundColor Yellow
    exit 1
}
& $ActivateScript

# Install dependencies
Write-Host "Installing dependencies..."
pip install -q -r (Join-Path $BackendDir "requirements.txt")

# Check for required environment variables
if (-not $env:SEARCH_API_KEY) {
    Write-Host "Warning: SEARCH_API_KEY not set. Please configure your environment." -ForegroundColor Yellow
}

# Start the backend
Set-Location $BackendDir
$Port = if ($env:BACKEND_PORT) { $env:BACKEND_PORT } else { "8000" }
Write-Host "Starting FastAPI server on port $Port..."
python main.py
