@echo off
chcp 65001 >nul
title svFilm 引擎（调色台的后端）
cd /d "%~dp0..\svFilm"

rem ============================================================
rem  svFilm 引擎 —— 调色台的后端。
rem  两个台子共用这一个引擎（调色台的窗口里点「渲染」也是连到这里）。
rem  ★ 平时不用手动起：直接开 svStudio，点「渲染」它会自己拉起来。
rem    这个 .bat 留给需要看引擎日志 / 单独重启引擎的时候用。
rem ============================================================

rem ⚠ Python 必须用 spektrafilm 那个环境 —— 真卷(spektrafilm)依赖 colour/rawpy 等，
rem   装默认环境里没有（09-14 踩过：起得来、/health 正常，但一 /render 就
rem   ModuleNotFoundError: No module named 'colour'）。
set "PY=C:\Users\user\.workbuddy\binaries\python\envs\spektrafilm\Scripts\python.exe"
if not exist "%PY%" (
  echo [错误] 找不到 spektrafilm 的 Python：
  echo   %PY%
  echo 请改这个 .bat 里的 PY 变量。
  pause
  exit /b 1
)

echo 正在起 svFilm 引擎（常驻，端口 8765）…
echo 关掉这个黑窗口 = 停引擎。调色台的「渲染」会连到这里。
echo.
"%PY%" -u -m svFilm.service --port 8765
pause
