@echo off
setlocal
cd /d "%~dp0"

echo Repo: %CD%

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 -c "import pandas,yfinance" >nul 2>&1
  if %ERRORLEVEL% NEQ 0 (
    echo Installing dependencies...
    py -3 -m pip install -r universal_quant\requirements.txt
  )
  echo Starting UPV dashboard...
  py -3 run_dashboard.py
  goto :end
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python -c "import pandas,yfinance" >nul 2>&1
  if %ERRORLEVEL% NEQ 0 (
    echo Installing dependencies...
    python -m pip install -r universal_quant\requirements.txt
  )
  echo Starting UPV dashboard...
  python run_dashboard.py
  goto :end
)

echo Python not found. Install Python 3 or run:
echo   py -3 run_dashboard.py
pause
exit /b 1

:end
if %ERRORLEVEL% NEQ 0 pause
