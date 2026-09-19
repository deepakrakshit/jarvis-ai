@echo off
setlocal enabledelayedexpansion
title JARVIS Personal AI Operating System

:: Change directory to current script root
cd /d "%~dp0"

:: If inside workspace root, check if jarvis directory exists
if exist "jarvis" cd "jarvis"

:: Resolve Python executable (prefer virtual environments if present, else system Python)
set PYTHON_CMD=python
if exist ".venv\Scripts\python.exe" (
    set PYTHON_CMD=.venv\Scripts\python.exe
) else if exist "..\.venv\Scripts\python.exe" (
    set PYTHON_CMD=..\.venv\Scripts\python.exe
)

:MENU
cls
echo ================================================================
echo                 JARVIS PERSONAL AI OPERATING SYSTEM
echo                           Version 3.0.0
echo ================================================================
echo.
echo   [1] Interactive Real-Time Dialogue (Chat REPL + Control Plane)
echo   [2] Live Multimodal Streaming Speech (Gemini 3.8 Live Voice)
echo   [3] Host Diagnostics & System Status (jarvis status)
echo   [4] Start Unified Daemon Server (Gateway + Heartbeat)
echo   [5] Run System Verification Test Suite (pytest -v)
echo   [6] Exit
echo.
echo ================================================================
set /p CHOICE="Select an option [1-6]: "

if "%CHOICE%"=="1" goto CHAT
if "%CHOICE%"=="2" goto LIVE
if "%CHOICE%"=="3" goto STATUS
if "%CHOICE%"=="4" goto SERVE
if "%CHOICE%"=="5" goto TEST
if "%CHOICE%"=="6" goto EXIT
goto MENU

:CHAT
cls
echo Starting JARVIS Interactive Dialogue...
"%PYTHON_CMD%" -m jarvis.cli chat
echo.
pause
goto MENU

:LIVE
cls
echo Connecting to Gemini 3.8 Live Streaming Session...
"%PYTHON_CMD%" -m jarvis.cli chat --live
echo.
pause
goto MENU

:STATUS
cls
"%PYTHON_CMD%" -m jarvis.cli status
echo.
pause
goto MENU

:SERVE
cls
echo Starting JARVIS Unified Daemon (WebSocket Gateway + Heartbeat)...
"%PYTHON_CMD%" -m jarvis.cli serve
echo.
pause
goto MENU

:TEST
cls
echo Executing JARVIS Verification Suite...
"%PYTHON_CMD%" -m pytest -v
echo.
pause
goto MENU

:EXIT
echo Exiting JARVIS. Operational continuity preserved.
exit /b 0
