@echo off
REM trading-agent.bat — Start the full scheduled agent (08:00 / 09:00 / 09:15 / 15:30 IST)
REM Place this file in the project root and double-click, or run from cmd.

cd /d "%~dp0"
python main.py --schedule
pause
