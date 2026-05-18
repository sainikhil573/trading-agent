"""
Entry point — runs the analysis immediately or schedules it daily at 8:00 AM IST.

Usage:
  python main.py          # run now (one-shot, useful for testing)
  python main.py --schedule   # block and run every day at 08:00 AM IST
"""

import sys
import os
import time
import logging
import argparse
from pathlib import Path

# Load .env before anything else
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))

from src.utils.logger import setup_logger

logger = setup_logger(name="trading_agent", log_dir="logs")


def _get_api_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        logger.error("ANTHROPIC_API_KEY not set. Copy .env.example -> .env and add your key.")
        sys.exit(1)
    return key


def run_once():
    """Run a single analysis cycle right now."""
    from src.orchestrator import run_analysis
    api_key = _get_api_key()
    try:
        run_analysis(api_key)
    except Exception as exc:
        logger.exception("Analysis cycle failed: %s", exc)


def run_scheduled():
    """Block forever and fire analysis every day at 08:00 AM IST."""
    import schedule
    import pytz
    from datetime import datetime

    IST = pytz.timezone("Asia/Kolkata")
    RUN_AT = "08:00"

    logger.info("Scheduler started — will run daily at %s IST", RUN_AT)
    logger.info("Press Ctrl-C to stop.")

    def _job():
        now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        logger.info("Scheduled trigger at %s", now_ist)
        run_once()

    schedule.every().day.at(RUN_AT).do(_job)

    # Show next scheduled run
    next_run = schedule.next_run()
    if next_run:
        next_ist = next_run.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")
        logger.info("Next run scheduled for: %s", next_ist)

    while True:
        try:
            schedule.run_pending()
            time.sleep(30)
        except KeyboardInterrupt:
            logger.info("Scheduler stopped by user.")
            break
        except Exception as exc:
            logger.exception("Scheduler error (will retry): %s", exc)
            time.sleep(60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Indian Stock Market AI Agent")
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run continuously, firing analysis every day at 08:00 AM IST",
    )
    args = parser.parse_args()

    if args.schedule:
        run_scheduled()
    else:
        run_once()
