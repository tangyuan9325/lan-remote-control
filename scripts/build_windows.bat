@echo off
REM Build Windows executables using PyInstaller
REM Usage: build_windows.bat
setlocal enabledelayedexpansion

echo === Building RemoteControl-Server (controlled side) ===
cd /d "%~dp0..\windows\server"
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --name RemoteControl-Server --clean main.py
if errorlevel 1 (
    echo Server build failed
    exit /b 1
)

echo.
echo === Building RemoteControl-Viewer (PyQt5 WebView) ===
cd /d "%~dp0..\windows\viewer_pyqt"
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --windowed --name RemoteControl-Viewer --clean ^
    --add-data "web;web" ^
    main.py
if errorlevel 1 (
    echo Viewer build failed
    exit /b 1
)

echo.
echo === Copying artifacts to build folder ===
cd /d "%~dp0.."
if not exist build mkdir build
copy /Y "windows\server\dist\RemoteControl-Server.exe" "build\lan-remote-control-v1.2.0-windows-server.exe"
copy /Y "windows\viewer_pyqt\dist\RemoteControl-Viewer.exe" "build\lan-remote-control-v1.2.0-windows-viewer.exe"

echo.
echo === Build complete ===
echo Server:  build\lan-remote-control-v1.2.0-windows-server.exe
echo Viewer:  build\lan-remote-control-v1.2.0-windows-viewer.exe
pause
