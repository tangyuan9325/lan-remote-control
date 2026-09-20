@echo off
echo 开始打包远程控制客户端...

REM 清理之前的构建
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist *.spec del *.spec

REM 生成 spec 文件
pyi-makespec --onefile --name "RemoteControlClient" --noconsole --icon=app.ico main.py

REM 修改 spec 文件（需要手动编辑或使用脚本）
echo 请手动编辑 RemoteControlClient.spec 文件，添加必要的 hiddenimports 和 hooks
pause

REM 执行打包
pyinstaller RemoteControlClient.spec

REM 清理临时文件
if exist build rmdir /s /q build
if exist *.spec del *.spec

echo.
echo 打包完成！可执行文件位于: dist\RemoteControlClient.exe
echo 大小: %~z1
pause