@echo off
REM Build Windows executables using PyInstaller
REM Usage: build_windows.bat

echo === Building RemoteControl-Server ===
cd /d "%~dp0..\windows\server"
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --name RemoteControl-Server --clean main.py
if errorlevel 1 (
    echo Server build failed
    exit /b 1
)

echo.
echo === Building RemoteControl-Viewer ===
cd /d "%~dp0..\windows\viewer"
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --name RemoteControl-Viewer --clean main.py
if errorlevel 1 (
    echo Viewer build failed
    exit /b 1
)

echo.
echo === Build complete ===
echo Server: windows\server\dist\RemoteControl-Server.exe
echo Viewer: windows\viewer\dist\RemoteControl-Viewer.exe
pause
