@echo off
echo ========================================
echo  Trading Agent - Task Scheduler Setup
echo ========================================
echo.
echo This will create an automatic daily run
echo at 10:30 PM EST (8:00 AM IST) Mon-Fri
echo.
echo Run this file as Administrator.
echo.

REM Delete existing task if present
schtasks /delete /tn "TradingAgentMorning" /f 2>nul

REM Create new scheduled task
schtasks /create /tn "TradingAgentMorning" ^
  /tr "\"D:\AI Agents\Claude Agents\trading-agent\trading-agent-auto.bat\"" ^
  /sc WEEKLY ^
  /d MON,TUE,WED,THU,FRI ^
  /st 22:30 ^
  /ru "%USERNAME%" ^
  /rl HIGHEST ^
  /f

echo.
echo Task created successfully.
echo Runs at: 10:30 PM EST every weekday
echo          = 8:00 AM IST next morning
echo.
schtasks /query /tn "TradingAgentMorning" /fo LIST
pause
