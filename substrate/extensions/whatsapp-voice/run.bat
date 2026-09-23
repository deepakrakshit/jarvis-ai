@echo off
setlocal enabledelayedexpansion

title JARVIS - WhatsApp AI Voice Console
cd /d "%~dp0"

echo ========================================================================
echo                  JARVIS WHATSAPP AI VOICE CONSOLE
echo ========================================================================
echo Project Directory: %CD%
echo.

:: Check Node.js
where node >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Node.js is not found on PATH. Please install Node.js 20 or higher.
    pause
    exit /b 1
)

:: Check ffmpeg
where ffmpeg >nul 2>nul
if %errorlevel% neq 0 (
    echo [WARNING] FFmpeg was not detected on PATH. WhatsApp audio encoding requires FFmpeg.
)

:: Install dependencies if node_modules is missing
if not exist "node_modules\" (
    echo [SETUP] Installing required dependencies...
    call npm install
    if %errorlevel% neq 0 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
)

:: Launch the interactive chat session
echo [LAUNCH] Initializing JARVIS interactive console...
echo.
call npx tsx src/chat/session.ts

if %errorlevel% neq 0 (
    echo.
    echo [NOTICE] Session ended with exit code %errorlevel%.
    pause
)
