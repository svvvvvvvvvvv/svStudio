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

rem ★ 这里故意用【相对路径】：
rem   早先写成 start "" "%EXE%" "%~dp0"，但 %~dp0 结尾自带反斜杠，
rem   拼出来是 ...\svStudio\" —— 反斜杠把后面的引号【转义】掉了，
rem   引号被吞进路径 ⇒ 报 "Unable to find Electron app at ...\svStudio""。
rem   现在 cd 到脚本所在目录，用 node_modules\... 相对路径，没有这个问题。

set "LOG=svstudio_启动日志.txt"
set "EXE=node_modules\electron\dist\electron.exe"

echo [%date% %time%] svStudio 启动 >> "%LOG%"
echo   工作目录: %CD% >> "%LOG%"
echo   Electron : %EXE% >> "%LOG%"

if not exist "%EXE%" (
  echo.
  echo [错误] 找不到 Electron：
  echo   %CD%\%EXE%
  echo.
  echo 解决办法：在下面这个目录里跑一次  npm install
  echo   %CD%
  echo.
  echo 详情见日志： %CD%\%LOG%
  echo.
  pause
  exit /b 1
)

echo 正在启动 svStudio...
echo   启动命令: "%EXE%" . >> "%LOG%"
start "" "%EXE%" .
echo svStudio 已打开，本窗口可以关掉。
echo   已发出启动命令 >> "%LOG%"
timeout /t 3 >nul
exit /b 0
