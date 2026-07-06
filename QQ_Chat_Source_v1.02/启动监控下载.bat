@echo off
chcp 65001 >nul
title QQ_Chat Minimal Monitor

cd /d "%~dp0"

if not exist "%~dp0ALL_Fold\logs" mkdir "%~dp0ALL_Fold\logs" 2>nul
if not exist "%~dp0ALL_Fold\errors" mkdir "%~dp0ALL_Fold\errors" 2>nul

if not exist "%~dp0python\python.exe" (
    echo [ERROR] Embedded Python was not found.
    pause
    exit /b 1
)

if not exist "%~dp0main.py" (
    echo [ERROR] main.py was not found.
    pause
    exit /b 1
)

echo ========================================
echo   QQ_Chat Minimal Monitor
echo ========================================
echo Starting...

"%~dp0python\python.exe" "%~dp0main.py" --minimal

if errorlevel 1 (
    echo.
    echo [ERROR] Monitor exited with an error.
    pause
)
