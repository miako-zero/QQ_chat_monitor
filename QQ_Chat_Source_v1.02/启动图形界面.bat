@echo off
chcp 65001 >nul
title QQ_Chat Monitor GUI
cd /d "%~dp0"

echo Loading QQ_Chat Monitor GUI...
echo The window will appear in a few seconds.
echo.
echo Tip: Closing the window will minimize it to the system tray.
echo      Right-click the tray icon and select "Exit" to quit completely.
echo.

if not exist "%~dp0python\pythonw.exe" (
    "%~dp0python\python.exe" "%~dp0main.py" --gui
    if errorlevel 1 (
        echo.
        echo [ERROR] GUI exited with an error.
        pause
    )
    exit /b
)

start "" /b "%~dp0python\pythonw.exe" "%~dp0main.py" --gui
timeout /t 2 /nobreak >nul
exit