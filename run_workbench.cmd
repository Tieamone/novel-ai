@echo off
setlocal
set "PYTHONUTF8=1"
chcp 65001 >nul
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo 未找到项目虚拟环境，请按 README.md 中的步骤安装依赖。
    pause
    exit /b 1
)
"venv\Scripts\python.exe" -B run_web.py %*
if errorlevel 1 pause
