@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM Prefer PowerShell launcher (clearer errors)
where powershell >nul 2>nul
if not errorlevel 1 (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0启动Web分析.ps1"
  if errorlevel 1 pause
  exit /b %errorlevel%
)
call "%~dp0启动Web分析.bat"
