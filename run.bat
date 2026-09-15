@echo off
chcp 65001 > nul
title JARVIS v1.0.0 - Stateful Personal AI Operating System

echo =====================================================================
echo   JARVIS v1.0.0: Stateful Personal AI Operating System
echo   Launching interactive terminal runtime...
echo =====================================================================
echo.

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m jarvis.cli %*
) else (
    python -m jarvis.cli %*
)

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] JARVIS terminated with error code %ERRORLEVEL%.
)

echo.
pause
