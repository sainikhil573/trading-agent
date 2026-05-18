"""
Entry point — runs analysis immediately or schedules it daily at IST times.

Usage:
  python main.py                  # run morning analysis now (one-shot)
  python main.py --preopen        # run pre-open check now (one-shot)
  python main.py --schedule       # block and run both every trading day

Scheduled runs (Asia/Kolkata timezone — correct regardless of laptop timezone):
  08:00 AM IST — full morning pre-market analysis
  09:00 AM IST — pre-open GO/WAIT/SKIP confirmation
"""

import sys
import os
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime, date

import pytz
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))

from src.utils.logger           import setup_logger
from src.utils.trading_calendar import is_trading_day

logger = setup_logger(name="trading_agent", log_dir="logs")

IST = pytz.timezone("Asia/Kolkata")


def _get_api_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        logger.error("ANTHROPIC_API_KEY not set. Copy .env.example -> .env and add your key.")
        sys.exit(1)
    return key


def run_morning_once() -> None:
    from src.orchestrator import run_morning_analysis
    try:
        run_morning_analysis(_get_api_key())
    except Exception:
        logger.exception("Morning analysis failed")


def run_preopen_once() -> None:
    from src.orchestrator import run_preopen_analysis
    try:
        run_preopen_analysis(_get_api_key())
    except Exception:
        logger.exception("Pre-open check failed")


def run_scheduled() -> None:
    """
    Timezone-aware scheduler loop.
    Compares current IST time every 30 s against target windows.
    Works correctly from any laptop timezone (Eastern, IST, UTC, etc.).
    """
    logger.info("Scheduler started — timezone: Asia/Kolkata (IST = UTC+5:30)")
    logger.info("Targets: 08:00 AM IST (morning) | 09:00 AM IST (pre-open)")
    logger.info("Press Ctrl-C to stop.")

    last_morning_date: date | None = None
    last_preopen_date: date | None = None

    while True:
        try:
            now_ist = datetime.now(IST)
            today   = now_ist.date()
            h, m    = now_ist.hour, now_ist.minute

            if is_trading_day(today):
                # 08:00 AM IST — morning window (fires once per day, m=0 only)
                if h == 8 and m == 0 and last_morning_date != today:
                    logger.info("Triggering 08:00 AM IST morning analysis (%s)", today)
                    run_morning_once()
                    last_morning_date = today

                # 09:00 AM IST — pre-open window
                elif h == 9 and m == 0 and last_preopen_date != today:
                    logger.info("Triggering 09:00 AM IST pre-open check (%s)", today)
                    run_preopen_once()
                    last_preopen_date = today

            else:
                # Log once when the 8 AM window hits on a holiday/weekend
                if h == 8 and m == 0:
                    logger.info("Today (%s) is a holiday or weekend — skipping", today)

            time.sleep(30)

        except KeyboardInterrupt:
            logger.info("Scheduler stopped by user.")
            break
        except Exception:
            logger.exception("Scheduler error — will retry in 60 s")
            time.sleep(60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Indian Stock Market AI Agent")
    parser.add_argument("--schedule", action="store_true",
                        help="Run continuously, triggering at 08:00 and 09:00 AM IST daily")
    parser.add_argument("--preopen",  action="store_true",
                        help="Run the 9 AM pre-open check right now (one-shot)")
    args = parser.parse_args()

    if args.schedule:
        run_scheduled()
    elif args.preopen:
        run_preopen_once()
    else:
        run_morning_once()
