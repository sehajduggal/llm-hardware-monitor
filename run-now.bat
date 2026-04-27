@echo off
REM Quick-run the LLM Hardware Monitor manually
cd /d "%~dp0"
echo Running LLM Hardware Monitor...
echo.
python monitor.py
echo.
echo Done! Check:
echo   - Desktop\LLM-Hardware-Monitor.html (dashboard)
echo   - %~dp0monitor.log (log)
echo   - %~dp0monitor_state.json (state)
echo.
pause
