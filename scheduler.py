"""
Scheduler — keeps running in background and triggers:
  9:15 AM  → place orders for yesterday's signals
  3:45 PM  → run scanner for tomorrow's signals
  4:00 PM  → send end of day summary

Usage:
    python scheduler.py          # run the scheduler (keep this running all day)
    python scheduler.py --once   # run morning session once immediately (for testing)
"""
import argparse
import time
from datetime import datetime

import notifier
import trader
from scanner import run_scan, print_report
import config


def is_market_day() -> bool:
    """Returns False on weekends."""
    return datetime.now().weekday() < 5  # 0=Mon, 4=Fri


def run_evening_scan():
    """Run the scanner after market close and save signals."""
    print(f"\n[{datetime.now().strftime('%H:%M')}] Running evening scan...")
    try:
        results = run_scan()
        print_report(results)
        if not results.empty:
            out_path = f"{config.DATA_DIR}/scan_{datetime.now().strftime('%Y%m%d')}.csv"
            results.to_csv(out_path, index=False)
            print(f"Signals saved to {out_path}")
        else:
            print("No signals found today.")
    except Exception as e:
        notifier.notify_error(f"Evening scan failed: {e}")


def run_eod_summary():
    """Send end of day portfolio summary."""
    import position_tracker as pt
    positions = pt.load_positions()
    notifier.notify_daily_summary([], positions)
    print(f"[{datetime.now().strftime('%H:%M')}] EOD summary sent.")


def scheduler_loop():
    print(f"Scheduler started — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("Mode:", "PAPER TRADE" if config.PAPER_TRADE else "⚠️ LIVE TRADE")
    print("Waiting for scheduled times...")
    print("  9:15 AM → Place orders")
    print("  3:45 PM → Evening scan")
    print("  4:00 PM → EOD summary")
    print("Press Ctrl+C to stop\n")

    morning_done = False
    scan_done    = False
    eod_done     = False

    while True:
        now = datetime.now()
        hhmm = now.hour * 100 + now.minute

        # Reset flags at midnight
        if hhmm == 0:
            morning_done = scan_done = eod_done = False

        if not is_market_day():
            time.sleep(60)
            continue

        # 9:15 AM — place orders
        if hhmm >= 915 and not morning_done:
            print(f"\n[{now.strftime('%H:%M')}] Market open — placing orders...")
            trader.run_morning_session()
            morning_done = True

        # 3:45 PM — evening scan
        elif hhmm >= 1545 and not scan_done:
            run_evening_scan()
            scan_done = True

        # 4:00 PM — EOD summary
        elif hhmm >= 1600 and not eod_done:
            run_eod_summary()
            eod_done = True

        time.sleep(30)  # check every 30 seconds


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true",
                        help="Run morning session once immediately")
    args = parser.parse_args()

    if args.once:
        trader.run_morning_session()
    else:
        try:
            scheduler_loop()
        except KeyboardInterrupt:
            print("\nScheduler stopped.")
