@echo off
REM trading-agent-manual.bat — Run morning analysis once and open dashboard
REM Use this for manual on-demand runs outside scheduled hours.

cd /d "%~dp0"
echo Select run mode:
echo   1  Morning analysis (now)
echo   2  Pre-open check (now)
echo   3  9:15 AM candle check (now)
echo   4  Post-market tracker (now)
echo   5  Dashboard only
echo.
set /p CHOICE="Enter 1-5: "

if "%CHOICE%"=="1" python main.py
if "%CHOICE%"=="2" python main.py --preopen
if "%CHOICE%"=="3" python main.py --candle
if "%CHOICE%"=="4" python main.py --postmarket
if "%CHOICE%"=="5" python main.py --dashboard
pause
