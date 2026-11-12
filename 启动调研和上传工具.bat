@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "ftp_config.json" (
    copy /Y "ftp_config.example.json" "ftp_config.json" >nul
)
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" workflow_ui.py
) else (
    python workflow_ui.py
)
if errorlevel 1 (
    echo 启动失败。请先安装 Python 3.11，并运行“安装依赖.bat”。
    pause
)
