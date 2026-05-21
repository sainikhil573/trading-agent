# Claude Code Project Instructions

Project: Indian F&O AI Trading Agent.

Goal:
Build a Python + Streamlit system for NSE F&O pre-market analysis, trade filtering, dashboard display, and post-market accuracy tracking.

Core stack:
- Python on Windows
- Claude API for analysis
- NSE/yfinance data fetchers
- Streamlit dashboard
- JSON outputs in data/processed
- Main pipeline starts from main.py

Project status file:
- Before starting any task, read docs/project_status.md first.
- Do not re-audit the full repository unless explicitly asked.
- Inspect only files relevant to the current task.
- After every meaningful feature or change, update docs/project_status.md:
  Current Stage, Completed Phases, Known Limitations, Next Recommended Phases, and Change Log.
- Keep docs/project_status.md concise. Do not turn it into a long report.

Important rules:
- Use minimal context and minimal output.
- Inspect only files relevant to the current task.
- Do not scan or rewrite unrelated files.
- Do not change existing JSON schema unless explicitly asked.
- Do not refactor the architecture unless explicitly asked.
- Prefer small, focused changes.
- Before editing, give a short plan.
- After editing, show changed files and test/run commands only.
- Keep explanations under 10 lines.

Common files:
- main.py: scheduler and app launcher
- src/fetchers/: NSE and global data fetchers
- src/analyzers/: signal, Claude, and post-market analysis
- src/orchestrator.py: pipeline runner
- src/dashboard/app.py: Streamlit dashboard
- data/processed/: generated daily JSON outputs