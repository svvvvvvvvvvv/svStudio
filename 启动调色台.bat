@echo off
chcp 65001 >nul
title svFilm 引擎（调色台的后端）

rem ============================================================
rem  svFilm 引擎 —— 调色台的后端。
rem  两个台子共用这一个引擎（调色台的窗口里点「渲染」也是连到这里）。
rem  ★ 平时不用手动起：直接开 svStudio，点「渲染」它会自己拉起来。
rem    这个 .bat 留给需要看引擎日志 / 单独重启引擎的时候用。
rem
rem  ★ 引擎现在就在**本仓库里**的 svFilm\ 子目录（git subtree 合进来的），
rem    所以 cd 到 %~dp0svFilm —— 整个目录搬到哪都能跑，不再依赖上级目录。
rem ============================================================

cd /d "%~dp0svFilm"
if not exist "svFilm\service.py" (
  echo [错误] 这里找不到 svFilm 引擎：
  echo    %CD%\svFilm\service.py
  echo  如果你是从 git 拉的，确认 svFilm 子目录在（git subtree 合进来的）。
  pause
  exit /b 1
)

rem ⚠ Python 优先用 spektrafilm 那个环境 —— 真卷(spektrafilm)依赖 colour/rawpy 等，
rem   装默认环境里没有（09-14 踩过：起得来、/health 正常，但一 /render 就
rem   ModuleNotFoundError: No module named colour）。
set "PY=C:/Users/user/.workbuddy/binaries/python/envs/spektrafilm/Scripts/python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -c "import colour" >nul 2>nul
if errorlevel 1 (
  echo [错误] 这份 Python 里没有 colour 库（spektrafilm 的依赖）：
  echo    %PY%
  echo  请改这个 .bat 里的 PY 变量，指向装了 spektrafilm 依赖的那个解释器。
  pause
  exit /b 1
)

echo 正在起 svFilm 引擎（常驻，端口 8765）…
echo 关掉这个黑窗口 = 停引擎。调色台的「渲染」会连到这里。
echo.
"%PY%" -u -m svFilm.service --port 8765
pause
