@echo off
cd /d "D:\AI Agents\Claude Agents\trading-agent"

REM Create logs directory if missing
if not exist logs mkdir logs

REM Set log filename with date
set LOGDATE=%date:~10,4%-%date:~4,2%-%date:~7,2%
set LOGFILE=logs\auto_run_%LOGDATE%.log

REM Log start time
echo ================================ >> "%LOGFILE%"
echo Run started: %date% %time% >> "%LOGFILE%"
echo ================================ >> "%LOGFILE%"

REM Run morning analysis (option 1 = silent auto mode)
echo 1 | python main.py >> "%LOGFILE%" 2>&1

REM Log completion
echo Run completed: %date% %time% >> "%LOGFILE%"
echo ================================ >> "%LOGFILE%"

REM Save last run status
echo {"last_run": "%LOGDATE%", "status": "completed"} > data\health\last_run_status.json
