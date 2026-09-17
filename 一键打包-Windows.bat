@echo off
chcp 65001 >nul
echo 正在准备打包 Windows 版 JanCode 智能体...
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0scripts\build-desktop.ps1"
echo.
echo 结束后可以关闭这个窗口。
pause
