@echo off
chcp 65001 >nul
title svFilm 引擎（调色台的后端）

rem ============================================================
rem  svFilm 引擎 —— 调色台的后端（不是台子本身，台子是 启动svStudio.bat）。
rem  两个台子共用这一个引擎（调色台的窗口里点「渲染」也是连到这里）。
rem  ★ 平时不用手动起：直接开 svStudio，点「渲染」它会自己拉起来。
rem    这个 .bat 留给需要看引擎日志 / 单独重启引擎的时候用。
rem
rem  ★ 引擎就在**本仓库里**的 svFilm\ 子目录（git subtree 合进来的），
rem    所以 cd 到 %~dp0svFilm —— 整个目录搬到哪都能跑，不依赖上级目录。
rem ============================================================

cd /d "%~dp0svFilm"
if not exist "svFilm\service.py" (
  echo [错误] 这里找不到 svFilm 引擎：
  echo    %CD%\svFilm\service.py
  echo  如果你是从 git 拉的，确认 svFilm 子目录在（git subtree 合进来的）。
  pause
  exit /b 1
)

rem ⚠ 必须用**装了 spektrafilm 依赖**的那个 Python（colour / rawpy / numpy）。
rem   用错环境的表现：引擎起得来、/health 也正常，但一 /render 就
rem   ModuleNotFoundError: No module named colour（09-14 踩过）。
rem
rem ★ 这里不写死任何本机路径。按顺序找：
rem     ① 环境变量 SVFILM_PY   ② PATH 上的 python
set "PY=%SVFILM_PY%"
if not defined PY set "PY=python"
"%PY%" -c "import colour" >nul 2>nul
if errorlevel 1 (
  echo [错误] 这份 Python 里没有 colour 库（spektrafilm 的依赖）:
  echo     %PY%
  echo  两种改法，任选一个:
  echo     甲. 设一次环境变量，然后**重开一个窗口**:
  echo         setx SVFILM_PY "你的 python.exe 完整路径"
  echo     乙. 直接改本文件里的 PY= 那一行。
  pause
  exit /b 1
)

echo 正在起 svFilm 引擎（常驻，端口 8765）…
echo 关掉这个黑窗口 = 停引擎。调色台的「渲染」会连到这里。
echo.
"%PY%" -u -m svFilm.service --port 8765
pause
