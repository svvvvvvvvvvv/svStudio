@echo off
chcp 65001 >nul
title svStudio
cd /d "%~dp0"

rem ============================================================
rem  svStudio = 选片台 + 调色台 二合一
rem  选片台：直接就能用（不依赖引擎）。
rem  调色台：需要 svFilm 引擎（本机 8765 端口）。这里先探一下：
rem          通 -> 直接开台子；不通 -> 谁点「渲染」谁去拉引擎，
rem          或直接双击同目录的「启动调色台.bat」。
rem ============================================================

if not exist "%~dp0node_modules\electron\dist\electron.exe" (
  echo [错误] 找不到 Electron：%~dp0node_modules\electron\dist\electron.exe
  echo        先在 svStudio 目录跑一次： npm install
  pause
  exit /b 1
)

echo 正在启动 svStudio...
start "" "%~dp0node_modules\electron\dist\electron.exe" "."
echo svStudio 已打开，本窗口可以关掉。
timeout /t 3 >nul
exit
