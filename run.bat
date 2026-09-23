@echo off
setlocal enabledelayedexpansion
title JARVIS Personal AI Operating System

:: Change directory to current script root
cd /d "%~dp0"

:: Ensure src directory is always on PYTHONPATH
set PYTHONPATH=%~dp0src;%PYTHONPATH%

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
echo                           Version 1.0.0
echo ================================================================
echo.
echo Initializing Gemini 3.8 Live session, background gateway, and native nodes...
echo.

"%PYTHON_CMD%" -m jarvis.cli chat --live --with-daemon

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo JARVIS live session exited with code %ERRORLEVEL%.
    pause
)
