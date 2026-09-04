@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 基本面分析 Web

echo ========================================
echo   基本面分析 Web 启动器
echo   地址: http://127.0.0.1:8765
echo ========================================
echo.

REM Prefer "py -3", then python, then python3
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  where python3 >nul 2>nul && set "PY=python3"
)
if not defined PY (
  echo [错误] 未检测到 Python。
  echo 请先安装 Python 3.10+：https://www.python.org/downloads/
  echo 安装时务必勾选 “Add python.exe to PATH”
  echo.
  pause
  exit /b 1
)

echo 使用解释器: %PY%
echo 正在安装依赖（首次可能较慢）...
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [错误] pip 安装失败。请把上方红色报错发我。
  pause
  exit /b 1
)

echo.
echo 正在启动服务，请不要关闭本窗口...
echo 浏览器将在服务就绪后自动打开。
echo.

REM Start server in background of this console group, then wait for health
start "mark-fundamentals-server" /b %PY% app.py

set /a tries=0
:waitloop
set /a tries+=1
if %tries% GTR 40 (
  echo.
  echo [错误] 40 秒内服务仍未就绪。
  echo 请看本窗口是否有 Python 报错；常见原因：
  echo   1. 未安装 Python / 没勾选 Add to PATH
  echo   2. 8765 端口被占用
  echo   3. 依赖安装失败
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/api/health -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>nul
if errorlevel 1 (
  timeout /t 1 /nobreak >nul
  goto waitloop
)

echo 服务已就绪，打开浏览器...
start "" "http://127.0.0.1:8765"

echo.
echo 服务运行中。关闭本窗口将停止服务。
echo 若页面打不开，手动访问: http://127.0.0.1:8765
echo.
pause
goto :eof