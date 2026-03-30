@echo off
cd /d "%~dp0"
echo Starting Startup Eval on http://localhost:5000
echo Press Ctrl+C to stop.
echo.
python app.py
pause
