@echo off
rem Double-click to start hr_ai. Arguments are passed on: start.bat -Cpu, start.bat stop
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
pause
