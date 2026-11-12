@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
py -3.11 -c "import sys" >nul 2>nul
if errorlevel 1 goto system_python
py -3.11 -m venv .venv
if errorlevel 1 goto failed
goto install
:system_python
python -c "import sys; assert sys.version_info >= (3, 11)" >nul 2>nul
if errorlevel 1 goto failed
python -m venv .venv
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
if not exist "ftp_config.json" copy /Y "ftp_config.example.json" "ftp_config.json" >nul
echo 安装完成。请确认已安装 Google Chrome，然后双击“启动调研和上传工具.bat”。
echo 需要 FTP 时，请向同事索取完整 ftp_config.json 并放入本目录。
exit /b 0
:failed
echo 安装失败。请先安装 Python 3.11（含 pip、venv 和 Tcl/Tk），并检查网络。
pause
exit /b 1
