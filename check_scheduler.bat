@echo off
echo Checking scheduled task status...
echo.
schtasks /query /tn "TradingAgentMorning" /fo LIST
echo.
echo Last run log:
if exist "logs\" (
  dir /b /o-d logs\auto_run_*.log 2>nul | head -1
) else (
  echo No logs found yet
)
echo.
echo Last run status:
if exist "data\health\last_run_status.json" (
  type "data\health\last_run_status.json"
) else (
  echo No status file yet
)
pause
