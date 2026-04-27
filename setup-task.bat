@echo off
REM ============================================================
REM LLM Hardware Monitor — Windows Task Scheduler Setup
REM ============================================================
REM This script registers a daily scheduled task that runs
REM monitor.py at 9:00 AM every day.
REM
REM Run this script as Administrator (right-click > Run as admin)
REM ============================================================

set TASK_NAME=LLM-Hardware-Monitor
set MONITOR_DIR=C:\Work\Personal\llm-hardware-monitor
set PYTHON_CMD=python

echo.
echo ============================================================
echo   LLM Hardware Monitor - Task Scheduler Setup
echo ============================================================
echo.

REM Check if task already exists
schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [!] Task "%TASK_NAME%" already exists.
    echo     Removing old task...
    schtasks /delete /tn "%TASK_NAME%" /f
    echo     Old task removed.
)

REM Create the scheduled task
echo [*] Creating scheduled task: %TASK_NAME%
echo     Schedule: Daily at 9:00 AM
echo     Script: %MONITOR_DIR%\monitor.py
echo.

schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "cmd /c cd /d \"%MONITOR_DIR%\" && %PYTHON_CMD% monitor.py" ^
    /sc DAILY ^
    /st 09:00 ^
    /rl HIGHEST ^
    /f

if %ERRORLEVEL% EQU 0 (
    echo.
    echo [OK] Task created successfully!
    echo.
    echo To test it now, run:
    echo   schtasks /run /tn "%TASK_NAME%"
    echo.
    echo To view task status:
    echo   schtasks /query /tn "%TASK_NAME%" /v
    echo.
    echo To remove the task:
    echo   schtasks /delete /tn "%TASK_NAME%" /f
) else (
    echo.
    echo [ERROR] Failed to create task. Try running as Administrator.
)

echo.
pause
