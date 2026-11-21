# 运行全部测试
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    & ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
    & ".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-dev.txt -q
}
& ".venv\Scripts\python.exe" -m pytest tests -q
