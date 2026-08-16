@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo   基本面分析 Web
echo   http://127.0.0.1:8765
echo ========================================
where python >nul 2>nul
if errorlevel 1 (
  echo 未找到 python，请先安装 Python 3.10+
  pause
  exit /b 1
)
python -m pip install -r requirements.txt -q
start "" "http://127.0.0.1:8765"
python app.py
pause
