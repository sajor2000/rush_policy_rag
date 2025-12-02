# Start script for the Next.js frontend (Windows PowerShell)
# Run with: .\start_frontend.ps1

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$FrontendDir = Join-Path $RepoRoot "apps\frontend"

Write-Host "Starting RUSH Policy RAG Frontend..." -ForegroundColor Green
Write-Host "====================================" -ForegroundColor Green

Set-Location $FrontendDir

# Install dependencies if node_modules doesn't exist
if (-not (Test-Path "node_modules")) {
    Write-Host "Installing dependencies..."
    npm install
}

Write-Host "Starting Next.js development server..."
npm run dev
