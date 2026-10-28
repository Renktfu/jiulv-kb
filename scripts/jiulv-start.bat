@echo off
rem 双击启动。会开一个命令行窗口跑服务，关窗口就停。
rem 第一次运行会自动建 .venv 并装依赖，大约一分钟，之后每次都是秒开。
rem
rem 这个文件本身不干正事，只做三件：定位项目目录、找一个能用的 python、交给
rem setup.py。装依赖、建索引、起服务、开浏览器全在 setup.py / jiulv 里，
rem macOS 那边共用同一份逻辑，改行为不用改两个地方。

rem 65001 是为了中文不乱码；不设的话 cp936 会把路径和提示打成问号。
chcp 65001 >nul 2>nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem 两个位置都要能跑：放在 scripts\ 下时项目在上一级，直接放在包根目录时就是自己。
set "ROOT=%~dp0"
if not exist "%ROOT%jiulv" set "ROOT=%~dp0.."
cd /d "%ROOT%"

rem 依次尝试：已有的 venv -> py 启动器 -> PATH 里的 python。
set "PY="
if exist "%ROOT%\.venv\Scripts\python.exe" set "PY=%ROOT%\.venv\Scripts\python.exe"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)

if not defined PY (
  echo 找不到 Python。
  echo.
  echo 去 https://www.python.org/downloads/windows/ 下载安装，
  echo 安装第一步一定要勾选 "Add python.exe to PATH"，然后重开这个窗口再双击。
  echo.
  pause
  exit /b 1
)

rem 交给自举脚本：建 .venv、装依赖，再把参数原样给 jiulv start。
%PY% scripts\setup.py %*
set "STATUS=%ERRORLEVEL%"

if not "%STATUS%"=="0" (
  echo.
  echo 退出码 %STATUS%。诊断环境：
  echo   %PY% scripts\setup.py --check
  echo   %PY% -m jiulv.cli doctor
  echo.
  pause
)
endlocal & exit /b %STATUS%
