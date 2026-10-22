@echo off
title SolidWorks MCP installer
cd /d "%~dp0"
where py >nul 2>nul && (py -3 setup_claude.py & goto end)
where python >nul 2>nul && (python setup_claude.py & goto end)
echo Python was not found. Install it from https://www.python.org/downloads/
echo (check "Add python.exe to PATH" during install), then run this again.
:end
echo.
pause
