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
from zoneinfo import ZoneInfo

import config
import notifier
import position_tracker as pt

MAX_HOLDING_DAYS = 10

IST = ZoneInfo("Asia/Kolkata")


def ist_now() -> datetime:
    """Current time in IST, regardless of server timezone (Railway runs UTC)."""
    return datetime.now(IST)

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


def calculate_new_stop(entry: float, initial_stop: float, stop: float, current_price: float) -> tuple:
    """
    Calculate new trailing stop based on R milestones.

    IMPORTANT: R-multiples must be calculated from the ORIGINAL stop
    (initial_stop) set at entry, NOT the current live stop. The live
    `stop` moves as trailing progresses (e.g. to breakeven at 0.5R) —
    if R were recalculated from that moved stop, risk_per_share would
    become 0 the moment stop reaches entry, and every future milestone
    (1.0R, 1.5R, 2.0R) would be permanently unreachable. This was a real
    bug: positions that hit breakeven never trailed any further.

    Returns (new_stop, milestone_label) or (None, None) if no update needed.
    """
    risk_per_share = entry - initial_stop
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
    symbol       = pos["symbol"]
    entry        = pos["entry"]
    stop         = pos["stop"]
    initial_stop = pos.get("initial_stop")

    if initial_stop is None:
        # Legacy position created before initial_stop existed — the
        # original risk is genuinely unrecoverable. Falling back to the
        # current stop reproduces the old frozen-at-breakeven behavior
        # for THIS position only (new positions are unaffected), and we
        # deliberately do NOT persist this fallback back into storage
        # (see position_tracker.py) so it stays visibly "unknown" rather
        # than looking like a real value.
        print(f"  ⚠️  {symbol}: no initial_stop on record (legacy position) — "
              f"trailing math falls back to current stop, may already be frozen. "
              f"Set Positions_DB!initial_stop for this row manually if you know "
              f"the original stop.")
        initial_stop = stop

    new_stop, label = calculate_new_stop(entry, initial_stop, stop, current_price)
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
                f"Trailing Stop Updated — {ist_now().strftime('%Y-%m-%d %H:%M')}\n\n"
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
                f"Time-Based Exit — {ist_now().strftime('%Y-%m-%d %H:%M')}\n\n"
                f"Stock:      {symbol}\n"
                f"Entry:      Rs.{entry:.2f}\n"
                f"Exit Price: Rs.{current_price:.2f}\n"
                f"Days Held:  {days_held} trading days\n"
                f"P&L:        Rs.{pnl:+,.0f}\n\n"
                f"Reason: No meaningful movement after {MAX_HOLDING_DAYS} trading days.\n"
            )
        )
    return True


def check_intraday_market_stress() -> dict:
    """Re-run the same sentiment scoring used at market open, but mid-day.
    Returns the sentiment result dict, or None if the check couldn't run
    (e.g. NSE API unavailable) — callers should treat None as 'no change,
    don't act on it' rather than assuming stress.

    This only READS sentiment — it never blocks new entries (that's
    trader.py's job at 9:10-9:30 AM only). It exists purely so open
    positions can react to a market-wide deterioration that happens
    AFTER the morning entry window, which nothing currently checks for.
    """
    try:
        from sentiment import run_sentiment_check
        return run_sentiment_check()
    except Exception as e:
        print(f"  [SENTIMENT] Intraday check failed: {e} — skipping stress tightening this cycle")
        return None


def apply_sentiment_stop_tightening(pos: dict, current_price: float, dry_run: bool = False) -> bool:
    """If market sentiment has deteriorated to SKIP level mid-day, raise
    the stop to breakeven (entry) — but ONLY for a position that is
    currently trading ABOVE entry (i.e. actually in profit right now).

    This is deliberately conservative: a position sitting below entry is
    still within its planned, pre-agreed risk (that's what the original
    stop is for) — forcing its stop up to breakeven while price is still
    below entry would trigger an immediate stop-out at today's price,
    turning a normal in-progress trade into a forced loss. That is NOT
    what this feature is for. It only locks in gains that already exist,
    it never manufactures a loss that wasn't already going to happen.

    Returns True if this position's stop was tightened.
    """
    symbol = pos["symbol"]
    entry  = pos["entry"]
    stop   = pos["stop"]

    if current_price <= entry:
        return False   # not currently in profit — leave existing stop alone
    if stop >= entry:
        return False    # already at/above breakeven — nothing to floor

    new_stop = entry
    print(f"  ⚠️  SENTIMENT TIGHTEN {symbol}: stop {stop:.2f} → {new_stop:.2f} (breakeven floor — market stress)")
    if not dry_run:
        all_positions = pt.load_positions()
        for p in all_positions:
            if p["symbol"] == symbol:
                p["stop"] = new_stop
        pt.save_positions(all_positions)
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

    if dry_run:
        mode = "DRY RUN"
    elif getattr(config, "PAPER_TRADE", True):
        mode = "PAPER"
    else:
        mode = "LIVE"
    print(f"\n{'='*60}")
    print(f"EXIT MANAGER — {ist_now().strftime('%Y-%m-%d %H:%M')} [{mode}]")
    print(f"Checking {len(positions)} positions...")
    print(f"{'='*60}")

    # Intraday market stress check — reuses the same sentiment scoring
    # used at 9:10-9:30 AM, but mid-day. Only ever tightens stops
    # (never blocks/places trades — that stays trader.py's job only).
    market_stressed = False
    if getattr(config, "INTRADAY_SENTIMENT_CHECK", True):
        sentiment_result = check_intraday_market_stress()
        if sentiment_result and sentiment_result.get("decision") == "SKIP":
            market_stressed = True
            print(f"  ⚠️  Mid-day sentiment deteriorated: {sentiment_result.get('verdict')}")
            print(f"  ⚠️  Tightening stops to breakeven on any position currently in profit.")

    trailing_updates   = []
    stop_exits         = []
    target_exits       = []
    time_exits         = []
    sentiment_tightened = []

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

        # 3. Mid-day market stress — floor stop at breakeven if in profit
        if market_stressed:
            if apply_sentiment_stop_tightening(pos, current_price, dry_run):
                sentiment_tightened.append(symbol)

        # 4. Trailing stop update (normal milestone logic still applies
        #    on top of any sentiment floor — it can only improve on it)
        trail_update = check_trailing_stop(pos, current_price, dry_run)
        if trail_update:
            trailing_updates.append(trail_update)

    print(f"\n{'='*60}")
    print(f"SUMMARY:")
    print(f"  Stop exits             : {len(stop_exits)}")
    print(f"  Target exits           : {len(target_exits)}")
    print(f"  Trailing stops updated : {len(trailing_updates)}")
    print(f"  Time-based exits       : {len(time_exits)}")
    if market_stressed:
        print(f"  Sentiment tightenings  : {len(sentiment_tightened)} (mid-day market stress)")
    if dry_run:
        print(f"\n  DRY RUN — no changes made")
    print(f"{'='*60}")

    if market_stressed and sentiment_tightened and not dry_run:
        notifier.send_email(
            subject=f"[SwingBot] Mid-Day Market Stress — Stops Tightened",
            body=(
                f"Mid-Day Sentiment Alert — {ist_now().strftime('%Y-%m-%d %H:%M')}\n\n"
                f"Market sentiment deteriorated to SKIP level during the day.\n"
                f"Stops tightened to breakeven on {len(sentiment_tightened)} "
                f"position(s) currently in profit:\n\n"
                + "\n".join(f"  - {s}" for s in sentiment_tightened) +
                f"\n\nThis only raises stops on profitable positions — it does not "
                f"place, close, or block any trades.\n"
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", "--dry-run", action="store_true")
    args = parser.parse_args()
    run_exit_checks(dry_run=args.test)