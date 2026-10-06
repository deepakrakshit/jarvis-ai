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
echo                           Version 1.0.1
echo ================================================================
echo.
echo Initializing Neural 3D Interface, Background Gateway, and Nodes...
echo.

if "%~1"=="" (
    "%PYTHON_CMD%" -m jarvis.cli chat --live --with-daemon
) else (
    "%PYTHON_CMD%" -m jarvis.cli %*
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo JARVIS session exited with code %ERRORLEVEL%.
    pause
)
