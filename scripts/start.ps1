$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    py -3 -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (Test-Path '.env') {
    Get-Content '.env' | ForEach-Object {
        if ($_ -match '^\s*([^#=\s]+)\s*=\s*(.*)\s*$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
        }
    }
}

if ([string]::IsNullOrWhiteSpace($env:GATEWAY_HOST)) { $env:GATEWAY_HOST = '127.0.0.1' }
if ([string]::IsNullOrWhiteSpace($env:GATEWAY_PORT)) { $env:GATEWAY_PORT = '9000' }

if ([string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)) {
    throw 'DEEPSEEK_API_KEY is missing. Copy .env.example to .env and set it.'
}

& .\.venv\Scripts\python.exe -m uvicorn llm_gateway.app:app --host $env:GATEWAY_HOST --port $env:GATEWAY_PORT
