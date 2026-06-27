"""
Cloud Scheduler — runs 24/7 on Railway.
Automatically handles all trading tasks at the right times.

Schedule:
  6:00 AM  → Auto login via TOTP
  9:10 AM  → Exit manager + sentiment + place orders
  9:15 AM - 3:30 PM → Monitor positions every 15 min
  3:45 PM  → EOD report + scan + sync sheets
  9:00 PM  → Evening sync (prices + sheets)
"""
import os
import time
from datetime import datetime, date

import config
from notifier import notify_error


def ist_now():
    """Get current IST time."""
    from datetime import timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist)


def is_market_day() -> bool:
    return ist_now().weekday() < 5  # Mon-Fri


def run_auth():
    print(f"[{ist_now().strftime('%H:%M')}] Running TOTP auth...")
    try:
        from groww_auth import get_token_via_totp, save_token
        token = get_token_via_totp()
        save_token(token)
        print("✅ Auth successful")
        return True
    except Exception as e:
        print(f"❌ Auth failed: {e}")
        notify_error(f"Auth failed: {e}")
        return False


def run_morning_trading():
    print(f"[{ist_now().strftime('%H:%M')}] Running morning trading session...")

    # Step 1: Run exit checks on existing positions
    try:
        from exit_manager import run_exit_checks
        run_exit_checks(dry_run=False)
    except Exception as e:
        print(f"Exit manager error: {e}")

    # Step 2: Check sentiment before placing new trades
    try:
        from sentiment import run_sentiment_check
        sentiment = run_sentiment_check()
        print(f"[SENTIMENT] Decision: {sentiment['verdict']}")
    except Exception as e:
        print(f"Sentiment check error: {e} — defaulting to REDUCE")
        sentiment = {"decision": "REDUCE", "size_multiplier": 0.5}

    # Step 3: Place trades — pass pre-computed sentiment (no double check in trader.py)
    try:
        from trader import run_morning_session
        run_morning_session(sentiment=sentiment)
    except Exception as e:
        print(f"Trading error: {e}")
        notify_error(f"Morning trading failed: {e}")


def run_position_monitor():
    print(f"[{ist_now().strftime('%H:%M')}] Monitoring positions...")
    try:
        from trader import check_positions
        check_positions()

        # Sync live prices to Google Sheets
        from eod_report import fetch_price
        from sheets import get_client, sync_positions
        import position_tracker as pt

        positions = pt.load_positions()
        if positions:
            prices = {}
            for pos in positions:
                data = fetch_price(pos["symbol"])
                prices[pos["symbol"]] = data.get("close", 0)

            client = get_client()
            workbook = client.open_by_key(config.GOOGLE_SHEET_ID)
            sync_positions(workbook, prices)
            print("✅ Positions synced to Sheets")
    except Exception as e:
        print(f"Monitor error: {e}")


def run_eod():
    print(f"[{ist_now().strftime('%H:%M')}] Running EOD tasks...")

    # EOD report email
    try:
        from eod_report import generate_report
        from notifier import send_email
        report = generate_report()
        print(report)
        send_email(
            subject=f"EOD Report — {ist_now().strftime('%Y-%m-%d')}",
            body=report,
        )
    except Exception as e:
        print(f"EOD report error: {e}")

    # Evening scan + sync directly to Sheets (don't rely on CSV file)
    scan_results = None
    try:
        from scanner import run_scan, print_report
        scan_results = run_scan()
        print_report(scan_results)
    except Exception as e:
        print(f"Scanner error: {e}")

    # Sync all sheets — pass scan_results directly so Signals tab is always fresh
    try:
        from sheets import sync_all
        sync_all(scan_results=scan_results)
    except Exception as e:
        print(f"Sheets sync error: {e}")


def run_evening_sync():
    """9 PM — lightweight sync of current prices and sheets."""
    print(f"[{ist_now().strftime('%H:%M')}] Running evening sync...")
    try:
        from eod_report import fetch_price
        from sheets import get_client, sync_positions
        import position_tracker as pt

        positions = pt.load_positions()
        if positions:
            prices = {}
            for pos in positions:
                data = fetch_price(pos["symbol"])
                prices[pos["symbol"]] = data.get("close", 0)

            client = get_client()
            workbook = client.open_by_key(config.GOOGLE_SHEET_ID)
            sync_positions(workbook, prices)
            print("✅ Evening sync complete")
        else:
            print("No open positions to sync")
    except Exception as e:
        print(f"Evening sync error: {e}")


def scheduler_loop():
    print("=" * 60)
    print("SWING BOT CLOUD SCHEDULER STARTED")
    print(f"Mode: {'PAPER TRADE' if config.PAPER_TRADE else '⚠️ LIVE TRADE'}")
    print(f"Time: {ist_now().strftime('%Y-%m-%d %H:%M')} IST")
    print("=" * 60)

    auth_done         = False
    morning_done      = False
    eod_done          = False
    evening_sync_done = False
    last_monitor      = 0
    current_date      = ist_now().date()

    while True:
        now  = ist_now()
        hhmm = now.hour * 100 + now.minute

        # Reset all flags on new calendar day (date-based, not hhmm==0)
        if now.date() != current_date:
            auth_done = morning_done = eod_done = evening_sync_done = False
            last_monitor = 0
            current_date = now.date()
            print(f"[{now.strftime('%H:%M')}] New day ({current_date}) — flags reset")

        if not is_market_day():
            time.sleep(60)
            continue

        # 6:00 AM — Auto login
        if hhmm >= 600 and not auth_done:
            auth_done = run_auth()

        # 9:10 AM — Morning trading (only if auth succeeded)
        elif hhmm >= 910 and hhmm < 930 and not morning_done:
            if auth_done:
                run_morning_trading()
                morning_done = True
            else:
                # Auth may have failed; retry once
                print(f"[{now.strftime('%H:%M')}] Auth not done — retrying before morning session")
                auth_done = run_auth()

        # 9:15 AM - 3:30 PM — Monitor every 15 minutes
        elif hhmm >= 915 and hhmm <= 1530:
            current_time = time.time()
            if current_time - last_monitor >= 900:  # 15 minutes
                run_position_monitor()
                last_monitor = current_time

        # 3:45 PM — EOD tasks
        elif hhmm >= 1545 and not eod_done:
            run_eod()
            eod_done = True

        # 9:00 PM — Evening sync
        elif hhmm >= 2100 and not evening_sync_done:
            run_evening_sync()
            evening_sync_done = True

        time.sleep(30)  # check every 30 seconds


if __name__ == "__main__":
    try:
        scheduler_loop()
    except KeyboardInterrupt:
        print("\nScheduler stopped.")
    except Exception as e:
        notify_error(f"Scheduler crashed: {e}")
        raise