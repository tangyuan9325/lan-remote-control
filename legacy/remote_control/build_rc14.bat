@echo off
chcp 65001 >nul
REM ============================================================
REM  RemoteControl v1.4 refactor — one-click PyInstaller build
REM  Fixes vs packeg.bat:
REM    - no manual .spec editing / findstr step (uses rc14.spec directly)
REM    - verifies python & pyinstaller first with clear errors
REM    - --collect-all for cv2/PyQt5/PIL dynamic imports
REM    - cleans stale build artifacts before packaging
REM ============================================================
cd /d "%~dp0"

where python >nul 2>nul || (echo [错误] 未找到 python，请安装并加入 PATH & exit /b 1)

python -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo [提示] 正在安装 PyInstaller...
    python -m pip install --yes pyinstaller || (echo [错误] PyInstaller 安装失败 & exit /b 1)
)

echo [1/3] 清理旧构建产物...
if exist build rmdir /s /q build
if exist dist\RemoteControl-v1.4-Claude.exe del /q dist\RemoteControl-v1.4-Claude.exe

echo [2/3] 开始打包（rc14.spec）...
python -m PyInstaller --noconfirm --clean rc14.spec
if errorlevel 1 (
    echo [错误] 打包失败，请检查上方日志
    pause
    exit /b 1
)

echo [3/3] 完成！
echo 输出文件: dist\RemoteControl-v1.4-Claude.exe
pause
