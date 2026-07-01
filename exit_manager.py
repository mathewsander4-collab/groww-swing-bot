"""
Exit Manager — handles all exit strategies:

1. STOP LOSS EXIT      — price hits stop level
2. TARGET EXIT         — price hits target level
3. BREAKEVEN STOP      — move stop to entry once 0.5R profit reached
4. TRAILING STOP       — dynamic trail at 1R, 1.5R, 2R milestones
5. TIME BASED EXIT     — exit if stock barely moved after N trading days

Trailing stop milestones:
  0.5R profit → move stop to breakeven (entry)
  1.0R profit → lock 50% of profit
  1.5R profit → lock 70% of profit
  2.0R profit → lock 90% of profit (close to target)

Runs every 15 minutes during market hours via cloud_scheduler.py

Usage:
    python exit_manager.py        # run checks on all positions
    python exit_manager.py --test # dry run, no changes
"""
import argparse
from datetime import datetime, timedelta

import config
import notifier
import position_tracker as pt

MAX_HOLDING_DAYS = 10

# Trailing stop milestones: (R_multiple, lock_pct)
TRAIL_MILESTONES = [
    (0.5, 0.0),   # breakeven — move stop to entry
    (1.0, 0.50),  # lock 50% of profit
    (1.5, 0.70),  # lock 70% of profit
    (2.0, 0.90),  # lock 90% of profit
]


def get_current_price(symbol: str) -> float:
    """Fetch current price using Groww historical candles."""
    from nse_price import get_ltp
    price = get_ltp(symbol)
    if price == 0:
        print(f"  [{symbol}] Price fetch failed")
    return price


def trading_days_held(entry_date_str: str) -> int:
    """Count trading days between entry date and today."""
    try:
        entry_date = datetime.strptime(entry_date_str, "%Y-%m-%d").date()
        today      = datetime.now().date()
        count      = 0
        current    = entry_date
        while current < today:
            current += timedelta(days=1)
            if current.weekday() < 5:
                count += 1
        return count
    except Exception:
        return 0


def calculate_new_stop(entry: float, stop: float, current_price: float) -> tuple:
    """
    Calculate new trailing stop based on R milestones.
    Returns (new_stop, milestone_label) or (None, None) if no update needed.
    """
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return None, None

    current_r = (current_price - entry) / risk_per_share

    # Find highest milestone reached
    best_stop  = None
    best_label = None

    for r_mult, lock_pct in TRAIL_MILESTONES:
        if current_r >= r_mult:
            if r_mult == 0.5:
                # Breakeven — move stop to entry
                candidate = entry
                label     = "breakeven"
            else:
                # Lock % of current profit
                candidate = entry + (current_price - entry) * lock_pct
                label     = f"{r_mult}R → lock {int(lock_pct*100)}%"
            best_stop  = candidate
            best_label = label

    if best_stop is None or best_stop <= stop:
        return None, None

    return round(best_stop, 2), best_label


def check_trailing_stop(pos: dict, current_price: float, dry_run: bool = False) -> dict:
    """Update trailing stop if price has moved to a new milestone."""
    symbol = pos["symbol"]
    entry  = pos["entry"]
    stop   = pos["stop"]

    new_stop, label = calculate_new_stop(entry, stop, current_price)
    if new_stop is None:
        return None

    profit_locked = (new_stop - entry) * pos["shares"]
    print(f"  TRAIL {symbol}: stop {stop:.2f} → {new_stop:.2f} ({label}) "
          f"price={current_price:.2f} locked=Rs.{profit_locked:,.0f}")

    if not dry_run:
        positions = pt.load_positions()
        for p in positions:
            if p["symbol"] == symbol:
                p["stop"]     = new_stop
                p["trailing"] = True
        pt.save_positions(positions)

        notifier.send_email(
            subject=f"Trailing Stop Updated — {symbol}",
            body=(
                f"Trailing Stop Updated — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                f"Stock:         {symbol}\n"
                f"Entry:         Rs.{entry:.2f}\n"
                f"Current:       Rs.{current_price:.2f}\n"
                f"Old Stop:      Rs.{stop:.2f}\n"
                f"New Stop:      Rs.{new_stop:.2f} ({label})\n"
                f"Profit locked: Rs.{profit_locked:,.0f}\n"
            )
        )

    return {"symbol": symbol, "old_stop": stop, "new_stop": new_stop, "label": label}


def check_stop_target(pos: dict, current_price: float, dry_run: bool = False) -> str:
    """Check if stop or target has been hit. Returns 'stop', 'target', or None."""
    symbol = pos["symbol"]
    entry  = pos["entry"]
    stop   = pos["stop"]
    target = pos["target"]
    shares = pos["shares"]

    if current_price <= stop:
        pnl = (stop - entry) * shares
        print(f"  STOP HIT {symbol}: price={current_price:.2f} <= stop={stop:.2f} P&L=Rs.{pnl:+,.0f}")
        if not dry_run:
            pt.remove_position(symbol, stop, "stop_hit")
            notifier.notify_exit(symbol, entry, stop, shares, "Stop Loss Hit")
        return "stop"

    if current_price >= target:
        pnl = (target - entry) * shares
        print(f"  TARGET HIT {symbol}: price={current_price:.2f} >= target={target:.2f} P&L=Rs.{pnl:+,.0f}")
        if not dry_run:
            pt.remove_position(symbol, target, "target_hit")
            notifier.notify_exit(symbol, entry, target, shares, "Target Hit")
        return "target"

    return None


def check_time_exit(pos: dict, current_price: float, dry_run: bool = False) -> bool:
    """Exit if stock hasn't moved meaningfully after MAX_HOLDING_DAYS."""
    symbol     = pos["symbol"]
    entry      = pos["entry"]
    entry_date = pos.get("entry_date", "")
    days_held  = trading_days_held(entry_date)

    if days_held < MAX_HOLDING_DAYS:
        return False

    price_change_pct = abs(current_price - entry) / entry * 100
    if price_change_pct > 2.0:
        return False

    pnl = (current_price - entry) * pos["shares"]
    print(f"  TIME EXIT {symbol}: {days_held} days ({(current_price-entry)/entry*100:+.1f}%) P&L=Rs.{pnl:+,.0f}")

    if not dry_run:
        pt.remove_position(symbol, current_price, "time_exit")
        notifier.send_email(
            subject=f"Time Exit — {symbol} after {days_held} days",
            body=(
                f"Time-Based Exit — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                f"Stock:      {symbol}\n"
                f"Entry:      Rs.{entry:.2f}\n"
                f"Exit Price: Rs.{current_price:.2f}\n"
                f"Days Held:  {days_held} trading days\n"
                f"P&L:        Rs.{pnl:+,.0f}\n\n"
                f"Reason: No meaningful movement after {MAX_HOLDING_DAYS} trading days.\n"
            )
        )
    return True


def run_exit_checks(dry_run: bool = False, prices: dict = None):
    """
    Run all exit checks on open positions.
    
    Args:
        dry_run: If True, print what would happen but make no changes.
        prices:  Optional dict of {symbol: price} to avoid refetching.
                 If None, fetches prices from Groww for each position.
    """
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
    stop_exits       = []
    target_exits     = []
    time_exits       = []

    for pos in positions:
        symbol = pos["symbol"]

        # Get price — use provided dict or fetch fresh
        if prices and symbol in prices:
            current_price = float(prices[symbol])
        else:
            current_price = get_current_price(symbol)

        if not current_price:
            print(f"  {symbol}: Could not fetch price — skipping")
            continue

        days_held = trading_days_held(pos.get("entry_date", ""))
        pnl       = (current_price - pos["entry"]) * pos["shares"]
        pnl_pct   = (current_price - pos["entry"]) / pos["entry"] * 100

        print(f"\n  {symbol}: Rs.{current_price:.2f} ({pnl_pct:+.1f}%) | "
              f"Days: {days_held} | P&L: Rs.{pnl:+,.0f}")

        # 1. Check stop/target first
        result = check_stop_target(pos, current_price, dry_run)
        if result == "stop":
            stop_exits.append(symbol)
            continue
        if result == "target":
            target_exits.append(symbol)
            continue

        # 2. Time exit
        if check_time_exit(pos, current_price, dry_run):
            time_exits.append(symbol)
            continue

        # 3. Trailing stop update
        trail_update = check_trailing_stop(pos, current_price, dry_run)
        if trail_update:
            trailing_updates.append(trail_update)

    print(f"\n{'='*60}")
    print(f"SUMMARY:")
    print(f"  Stop exits             : {len(stop_exits)}")
    print(f"  Target exits           : {len(target_exits)}")
    print(f"  Trailing stops updated : {len(trailing_updates)}")
    print(f"  Time-based exits       : {len(time_exits)}")
    if dry_run:
        print(f"\n  DRY RUN — no changes made")
    print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", "--dry-run", action="store_true")
    args = parser.parse_args()
    run_exit_checks(dry_run=args.test)