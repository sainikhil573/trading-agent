# Indian F&O AI Trading Agent

Python + Streamlit system for NSE F&O pre-market analysis, trade filtering, dashboard display, and post-market accuracy tracking.

## Quick Start

```
python main.py               # morning analysis now
python main.py --preopen     # pre-open check now
python main.py --candle      # 9:15 AM candle check now
python main.py --postmarket  # post-market grading now
python main.py --schedule    # block and run all four at IST times
python main.py --dashboard   # launch Streamlit dashboard only
```

Or double-click `trading-agent-manual.bat` for an interactive menu.

---

## Automated Setup (One Time)

Sets up Windows Task Scheduler to run morning analysis automatically every weekday at **10:30 PM EST = 8:00 AM IST**.

**Step 1:** Right-click `setup_task_scheduler.bat`  
**Step 2:** Select "Run as administrator"  
**Step 3:** Click Yes on UAC prompt  
**Step 4:** Done — agent runs automatically every weekday at 10:30 PM EST (8 AM IST)

To verify it is set up:

    Double-click check_scheduler.bat

To check results next morning:

    Open browser: http://localhost:8501
    Or check:     logs\auto_run_YYYY-MM-DD.log

To disable:

    schtasks /delete /tn "TradingAgentMorning" /f

---

## Requirements

- Python 3.11+
- `.env` file with `ANTHROPIC_API_KEY` (copy from `.env.example`)
- `pip install -r requirements.txt`
