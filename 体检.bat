@echo off
chcp 65001 >nul
title svStudio 体检
cd /d "%~dp0"
setlocal

rem 起不来的时候双击这个 —— 它会把每步结果打出来、最后停在窗口上不关。
echo ============================================
echo   svStudio 启动体检
echo ============================================
echo.

echo [1/4] 当前目录
echo       %CD%
echo.

echo [2/4] Electron 可执行文件
set "EXE=%~dp0node_modules\electron\dist\electron.exe"
if exist "%EXE%" (
  echo       找到：%EXE%
) else (
  echo       [!] 没找到：%EXE%
  echo           去这个目录跑一次 npm install：%~dp0
  echo.
  pause
  exit /b 1
)
echo.

echo [3/4] 主进程能不能加载（只检查语法/依赖，不开窗口）
"%~dp0node_modules\electron\dist\electron.exe" --version
echo       上面这行应该打出 33.x 的版本号。
echo.

echo [4/4] 真正启动（窗口应该会弹出来）
start "" "%EXE%" "%~dp0"
echo       如果窗口没弹出来，把上面所有文字截图发我。
echo.
pause
