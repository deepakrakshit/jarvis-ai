@echo off
chcp 65001 > nul
title JARVIS v1.0.0 - Stateful Personal AI Operating System

echo =====================================================================
echo   JARVIS v1.0.0: Stateful Personal AI Operating System
echo =====================================================================
echo.

if not "%~1"=="" (
    goto :RUN_DIRECT
)

echo Select Runtime Mode:
echo   [1] Realtime Voice Plane (Gemini 3.8 Live + Hardware Mic/Speaker)
echo   [2] Interactive Terminal Text CLI
echo.
set /p MODE_CHOICE="Enter selection [1 or 2, default: 1]: "

if "%MODE_CHOICE%"=="2" (
    set ARGS=
) else (
    set ARGS=--voice
)

:RUN_DIRECT
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m jarvis.cli %* %ARGS%
) else (
    python -m jarvis.cli %* %ARGS%
)

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] JARVIS terminated with error code %ERRORLEVEL%.
)

echo.
pause
