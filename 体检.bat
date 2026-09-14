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

echo [1/5] 当前目录
echo       %CD%
echo.

echo [2/5] Electron 可执行文件
set "EXE=node_modules\electron\dist\electron.exe"
if exist "%EXE%" (
  echo       找到：%CD%\%EXE%
) else (
  echo       [!] 没找到：%CD%\%EXE%
  echo           去这个目录跑一次 npm install：%CD%
  echo.
  pause
  exit /b 1
)
echo.

echo [3/5] Electron 版本
"%EXE%" --version
echo       上面这行应该打出 33.x 的版本号。
echo.

echo [4/5] 界面产物
if exist "renderer\dist\index.js" (
  echo       找到 renderer\dist\index.js
) else (
  echo       [!] 没有 renderer\dist\index.js —— 先在下面这个目录跑 npm run ui:build
  echo           %CD%
)
echo.

echo [5/5] 真正启动（窗口应该会弹出来）
rem ★ 这里故意用相对路径 "."：%~dp0 自带尾反斜杠，写成 "%~dp0" 会
rem   把引号转义掉、引号被吞进路径 ⇒ Electron 报 Unable to find app at ...\svStudio"
start "" "%EXE%" .
echo       如果窗口没弹出来，把上面所有文字截图发我。
echo.
pause
