@echo off
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_devteam.ps1" %*

if errorlevel 1 (
  echo.
  echo Startup failed. Review the error message above.
  pause
)
