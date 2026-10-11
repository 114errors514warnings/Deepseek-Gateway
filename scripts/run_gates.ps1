# DeepSeekGateway · 一键门禁（Windows PowerShell）
# 用法：  .\scripts\run_gates.ps1
# 退出码：0 = 全 PASS/NO_GATE/BASELINE；1 = 存在 FAIL（可直接作 CI 门禁）
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$python = if (Test-Path '.venv\Scripts\python.exe') { '.venv\Scripts\python.exe' } else { 'python' }

Write-Host '[1/2] L1 功能/单元测试 (pytest)'
& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { Write-Host 'L1 FAIL'; exit 1 }

Write-Host '[2/2] L2 阶段门禁 (acceptance)'
& $python scripts\run_acceptance.py
exit $LASTEXITCODE
