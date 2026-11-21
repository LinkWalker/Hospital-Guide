# 一键启动：自动创建虚拟环境、安装依赖、启动服务
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[1/3] 创建虚拟环境 ..."
    python -m venv .venv
    & ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
    Write-Host "[2/3] 安装依赖 ..."
    & ".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
} else {
    Write-Host "[1/3] 虚拟环境已存在"
}
Write-Host "[3/3] 启动服务: http://127.0.0.1:8000  (演示页: /  接口文档: /docs)"
& ".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
