@echo off
chcp 65001 >nul
cd /d "%~dp0..\svFilm"
echo 正在起 svFilm 引擎（常驻）…
echo 起好后浏览器打开： http://127.0.0.1:8765/
echo 关掉这个黑窗口 = 停引擎。
echo.
start "" http://127.0.0.1:8765/
"C:/Users/user/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -u -m svFilm.service --port 8765 --web "%~dp0web"
pause
