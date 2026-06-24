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
from datetime import datetime

import config
from notifier import send_email, notify_error


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
    try:
        from exit_manager import run_exit_checks
        run_exit_checks(dry_run=False)
    except Exception as e:
        print(f"Exit manager error: {e}")

    try:
        from trader import run_morning_session
        run_morning_session()
    except Exception as e:
        print(f"Trading error: {e}")
        notify_error(f"Morning trading failed: {e}")


def run_position_monitor():
    print(f"[{ist_now().strftime('%H:%M')}] Monitoring positions...")
    try:
        from trader import check_positions
        check_positions()

        # Sync prices to Google Sheets
        from eod_report import fetch_price
        from sheets import get_client, sync_positions
        import position_tracker as pt

        positions = pt.load_positions()
        if positions:
            prices = {}
            for pos in positions:
                data = fetch_price(pos["symbol"])
                prices[pos["symbol"]] = data.get("close", 0)

            client   = get_client()
            workbook = client.open_by_key(config.GOOGLE_SHEET_ID)
            sync_positions(workbook, prices)
            print("✅ Positions synced to Sheets")
    except Exception as e:
        print(f"Monitor error: {e}")


def run_eod():
    print(f"[{ist_now().strftime('%H:%M')}] Running EOD tasks...")
    try:
        from eod_report import generate_report
        from notifier import send_email
        report = generate_report()
        print(report)
        send_email(
            subject=f"EOD Report — {ist_now().strftime('%Y-%m-%d')}",
            body=report
        )
    except Exception as e:
        print(f"EOD report error: {e}")

    try:
        from scanner import run_scan, print_report
        results = run_scan()
        print_report(results)
        if not results.empty:
            out_path = f"{config.DATA_DIR}/scan_{ist_now().strftime('%Y%m%d')}.csv"
            results.to_csv(out_path, index=False)
    except Exception as e:
        print(f"Scanner error: {e}")

    try:
        from sheets import sync_all
        sync_all()
    except Exception as e:
        print(f"Sheets sync error: {e}")


def scheduler_loop():
    print("=" * 60)
    print("SWING BOT CLOUD SCHEDULER STARTED")
    print(f"Mode: {'PAPER TRADE' if config.PAPER_TRADE else '⚠️ LIVE TRADE'}")
    print(f"Time: {ist_now().strftime('%Y-%m-%d %H:%M')} IST")
    print("=" * 60)

    auth_done    = False
    morning_done = False
    eod_done     = False

    # Track last monitor time
    last_monitor = 0

    while True:
        now  = ist_now()
        hhmm = now.hour * 100 + now.minute

        # Reset flags at midnight
        if hhmm == 0:
            auth_done = morning_done = eod_done = False
            last_monitor = 0
            print(f"[{now.strftime('%H:%M')}] New day — flags reset")

        if not is_market_day():
            time.sleep(60)
            continue

        # 6:00 AM — Auto login
        if hhmm >= 600 and not auth_done:
            auth_done = run_auth()

        # 9:10 AM — Morning trading
        elif hhmm >= 910 and hhmm < 930 and not morning_done and auth_done:
            run_morning_trading()
            morning_done = True

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

        time.sleep(30)  # check every 30 seconds


if __name__ == "__main__":
    try:
        scheduler_loop()
    except KeyboardInterrupt:
        print("\nScheduler stopped.")
    except Exception as e:
        notify_error(f"Scheduler crashed: {e}")
        raise
