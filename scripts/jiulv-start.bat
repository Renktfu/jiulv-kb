@echo off
rem Windows 双击启动。双击后开一个命令行窗口跑服务，关窗口就停。
rem 开机自启：python -m jiulv.cli autostart on
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

rem 依次尝试：项目 venv → py 启动器 → PATH 里的 python。
set "PY="
if exist "%~dp0..\.venv\Scripts\python.exe" set "PY=%~dp0..\.venv\Scripts\python.exe"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo 找不到 Python。先装 Python 3.9 以上，安装时勾选 "Add python.exe to PATH"。
  pause
  exit /b 1
)

rem 光找到 python 不够，还得确认依赖在。挑错解释器时这里会明确拦下来，
rem 而不是让它在 import 那一步抛一堆栈。
%PY% -c "import yaml, fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo 当前 Python 里缺依赖。在项目目录执行：
  echo.
  echo   %PY% -m venv .venv
  echo   .venv\Scripts\pip install -e ".[parsers]"
  echo.
  echo 装完再双击这个文件；诊断环境：.venv\Scripts\python -m jiulv.cli doctor
  pause
  exit /b 1
)

%PY% -m jiulv.cli start %*

if errorlevel 1 (
  echo.
  echo 启动失败。先跑这一句看环境：%PY% -m jiulv.cli doctor
  pause
)
endlocal
