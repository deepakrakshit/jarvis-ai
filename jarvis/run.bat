@echo off
setlocal enabledelayedexpansion
title JARVIS Personal AI Operating System

:: Change directory to current script root
cd /d "%~dp0"

:: Resolve Python executable (prefer virtual environments if present, else system Python)
set PYTHON_CMD=python
if exist ".venv\Scripts\python.exe" (
    set PYTHON_CMD=.venv\Scripts\python.exe
) else if exist "..\.venv\Scripts\python.exe" (
    set PYTHON_CMD=..\.venv\Scripts\python.exe
)

cls
echo ================================================================
echo             BOOTING JARVIS PERSONAL AI OPERATING SYSTEM
echo                           Version 3.0.0
echo ================================================================
echo.
echo Initializing subsystems, background gateway, and native nodes...
echo.

"%PYTHON_CMD%" -m jarvis.cli chat --with-daemon

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo JARVIS session exited with code %ERRORLEVEL%.
    pause
)
