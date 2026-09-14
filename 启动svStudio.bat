@echo off
chcp 65001 >nul
title svStudio
cd /d "%~dp0"
setlocal

rem ============================================================
rem  svStudio = 选片台 + 调色台 二合一
rem  选片台：直接能用（不依赖引擎）。
rem  调色台：需要 svFilm 引擎（本机 8765）。没起的话，点「渲染」台子自己拉。
rem ============================================================

set "LOG=%~dp0svstudio_启动日志.txt"
echo [%date% %time%] svStudio 启动 >> "%LOG%"
echo   工作目录: %CD% >> "%LOG%"

set "EXE=%~dp0node_modules\electron\dist\electron.exe"
echo   查找 Electron: %EXE% >> "%LOG%"

if not exist "%EXE%" (
  echo.
  echo [错误] 找不到 Electron：
  echo   %EXE%
  echo.
  echo 解决办法：在下面这个目录里跑一次  npm install
  echo   %~dp0
  echo.
  echo 详情见日志： %LOG%
  echo.
  pause
  exit /b 1
)

echo   已找到，正在启动... >> "%LOG%"
echo 正在启动 svStudio...
start "" "%EXE%" "%~dp0"

if errorlevel 1 (
  echo.
  echo [错误] Electron 启动失败，看日志： %LOG%
  echo.
  pause
  exit /b 1
)

echo svStudio 已打开，本窗口可以关掉。
timeout /t 3 >nul
exit /b 0
