"""
Exit Manager — handles two additional exit strategies:

1. TRAILING STOP LOSS
   - Once stock moves 1R in profit (entry + 1x risk)
   - Stop automatically trails up to lock in profit
   - Updates stop in position_tracker

2. TIME BASED EXIT
   - If trade hasn't moved meaningfully after N days
   - Automatically exits to free up capital
   - Default: 10 trading days

Usage:
    python exit_manager.py          # run checks on all open positions
    python exit_manager.py --test   # dry run, no actual exits
"""
import argparse
import os
from datetime import datetime, timedelta

import pandas as pd

import config
import notifier
import position_tracker as pt
from nse import NSE

MAX_HOLDING_DAYS  = 10    # exit if no meaningful move after this many trading days
TRAIL_TRIGGER_R   = 1.0   # start trailing after 1R profit (entry + 1x risk distance)
TRAIL_LOCK_PCT    = 0.5   # trail stop locks in 50% of current profit


def get_nse():
    download_dir = os.path.join(config.DATA_DIR, "nse_downloads")
    os.makedirs(download_dir, exist_ok=True)
    return NSE(download_dir)


def get_current_price(symbol: str) -> float:
    """Fetch current price from NSE."""
    try:
        nse  = get_nse()
        data = nse.equityQuote(symbol)
        return float(data.get("close", 0) or data.get("open", 0))
    except:
        return 0.0


def trading_days_held(entry_date_str: str) -> int:
    """Count trading days between entry date and today."""
    try:
        entry_date = datetime.strptime(entry_date_str, "%Y-%m-%d").date()
        today      = datetime.now().date()
        count      = 0
        current    = entry_date
        while current < today:
            current += timedelta(days=1)
            if current.weekday() < 5:  # Mon-Fri only
                count += 1
        return count
    except:
        return 0


def check_trailing_stop(pos: dict, current_price: float, dry_run: bool = False) -> dict:
    """
    Check if trailing stop should be updated.
    Returns updated position dict or None if no change.
    """
    symbol    = pos["symbol"]
    entry     = pos["entry"]
    stop      = pos["stop"]
    target    = pos["target"]

    risk_per_share   = entry - stop
    reward_per_share = target - entry

    # Only trail after 1R profit
    trail_trigger_price = entry + (TRAIL_TRIGGER_R * risk_per_share)

    if current_price < trail_trigger_price:
        return None  # not in profit enough to trail yet

    # Calculate new trailing stop
    current_profit   = current_price - entry
    locked_profit    = current_profit * TRAIL_LOCK_PCT
    new_stop         = entry + locked_profit

    # Only move stop UP never down
    if new_stop <= stop:
        return None

    new_stop = round(new_stop, 2)

    print(f"  📈 {symbol}: Trailing stop updated {stop:.2f} → {new_stop:.2f} "
          f"(price: {current_price:.2f}, profit locked: ₹{locked_profit:.2f}/share)")

    if not dry_run:
        # Update position in tracker
        positions = pt.load_positions()
        for p in positions:
            if p["symbol"] == symbol:
                p["stop"] = new_stop
                p["trailing"] = True
        pt.save_positions(positions)

        # Send email
        notifier.send_email(
            subject=f"Trailing Stop Updated — {symbol}",
            body=f"""
Trailing Stop Updated — {datetime.now().strftime('%Y-%m-%d %H:%M')}

Stock:     {symbol}
Entry:     ₹{entry:.2f}
Current:   ₹{current_price:.2f}
Old Stop:  ₹{stop:.2f}
New Stop:  ₹{new_stop:.2f}
Profit locked: ₹{(new_stop - entry) * pos['shares']:,.0f}

Position is now protected — minimum profit secured.
"""
        )

    return {"symbol": symbol, "old_stop": stop, "new_stop": new_stop}


def check_time_exit(pos: dict, current_price: float, dry_run: bool = False) -> bool:
    """
    Check if position should be exited due to time limit.
    Returns True if position was/should be exited.
    """
    symbol      = pos["symbol"]
    entry       = pos["entry"]
    entry_date  = pos.get("entry_date", "")
    days_held   = trading_days_held(entry_date)

    if days_held < MAX_HOLDING_DAYS:
        return False

    # Check if position has moved meaningfully (>2% either way)
    price_change_pct = abs(current_price - entry) / entry * 100
    if price_change_pct > 2.0:
        return False  # position is moving — let it play out

    # Position is stuck — exit to free capital
    pnl = (current_price - entry) * pos["shares"]
    print(f"  ⏰ {symbol}: Time exit after {days_held} days "
          f"(price barely moved: {(current_price-entry)/entry*100:+.1f}%) "
          f"P&L: ₹{pnl:+,.0f}")

    if not dry_run:
        pt.remove_position(symbol, current_price, "time_exit")
        notifier.send_email(
            subject=f"Time Exit — {symbol} after {days_held} days",
            body=f"""
Time-Based Exit — {datetime.now().strftime('%Y-%m-%d %H:%M')}

Stock:      {symbol}
Entry:      ₹{entry:.2f}
Exit Price: ₹{current_price:.2f}
Days Held:  {days_held} trading days
P&L:        ₹{pnl:+,.0f}

Reason: Stock hasn't moved meaningfully after {MAX_HOLDING_DAYS} trading days.
Capital freed up for better opportunities.
"""
        )

    return True


def run_exit_checks(dry_run: bool = False):
    """Run all exit checks on open positions."""
    positions = pt.load_positions()
    if not positions:
        print("No open positions to check.")
        return

    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"\n{'='*60}")
    print(f"EXIT MANAGER — {datetime.now().strftime('%Y-%m-%d %H:%M')} [{mode}]")
    print(f"Checking {len(positions)} positions...")
    print(f"{'='*60}")

    trailing_updates = []
    time_exits       = []

    for pos in positions:
        symbol        = pos["symbol"]
        current_price = get_current_price(symbol)

        if not current_price:
            print(f"  {symbol}: Could not fetch price — skipping")
            continue

        days_held = trading_days_held(pos.get("entry_date", ""))
        pnl       = (current_price - pos["entry"]) * pos["shares"]
        pnl_pct   = (current_price - pos["entry"]) / pos["entry"] * 100

        print(f"\n  {symbol}: ₹{current_price:.2f} ({pnl_pct:+.1f}%) | "
              f"Days held: {days_held} | P&L: ₹{pnl:+,.0f}")

        # Check time exit first
        if check_time_exit(pos, current_price, dry_run):
            time_exits.append(symbol)
            continue

        # Check trailing stop
        trail_update = check_trailing_stop(pos, current_price, dry_run)
        if trail_update:
            trailing_updates.append(trail_update)

    print(f"\n{'='*60}")
    print(f"SUMMARY:")
    print(f"  Trailing stops updated : {len(trailing_updates)}")
    print(f"  Time-based exits       : {len(time_exits)}")
    if time_exits:
        print(f"  Exited stocks          : {', '.join(time_exits)}")
    if dry_run:
        print(f"\n  ⚠️ DRY RUN — no actual changes made")
    print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", "--dry-run", action="store_true",
                        help="Dry run — show what would happen without making changes")
    args = parser.parse_args()
    run_exit_checks(dry_run=args.test)
