"""
Auto trader — runs the full trading workflow:
1. Checks market sentiment (VIX, Nifty, FII, A/D ratio)
2. Loads yesterday's signals
3. Places BUY orders at market open (9:15 AM)
4. Places stop loss and target orders immediately after
5. Sends email alerts

Paper trade mode (PAPER_TRADE = True in config):
→ Simulates everything without placing real orders

Usage:
    python trader.py          # run once manually
    python trader.py --test   # test email only
    python trader.py --check  # check open positions
    python trader.py --positions # print open positions
"""
import argparse
import glob
import os
import time
from datetime import datetime

import pandas as pd

import config
import notifier
import position_tracker as pt
from groww_client import GrowwClient
from sentiment import run_sentiment_check


def load_latest_signals() -> list:
    pattern = os.path.join(config.DATA_DIR, "scan_*.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        return []
    df = pd.read_csv(files[-1])
    return df.to_dict("records")


def place_buy_order(client: GrowwClient, signal: dict, size_multiplier: float = 1.0) -> str:
    symbol = signal["symbol"]
    shares = max(1, int(int(signal.get("shares", 0)) * size_multiplier))
    entry  = float(signal["entry"])

    if config.PAPER_TRADE:
        print(f"[PAPER] BUY {shares} shares of {symbol} @ ₹{entry:.2f}")
        return "PAPER"

    resp     = client.place_order(symbol=symbol, quantity=shares,
                                  transaction_type="BUY", order_type="LIMIT", price=entry)
    order_id = resp.get("order_id", "UNKNOWN")
    print(f"[LIVE] BUY {symbol} | {shares} shares @ ₹{entry:.2f} | ID: {order_id}")
    return order_id


def place_stop_loss_order(client: GrowwClient, symbol: str, shares: int, stop: float) -> str:
    if config.PAPER_TRADE:
        print(f"[PAPER] STOP LOSS {symbol} @ ₹{stop:.2f}")
        return "PAPER_SL"
    resp = client.place_order(symbol=symbol, quantity=shares,
                              transaction_type="SELL", order_type="SL", price=stop)
    return resp.get("order_id", "UNKNOWN")


def place_target_order(client: GrowwClient, symbol: str, shares: int, target: float) -> str:
    if config.PAPER_TRADE:
        print(f"[PAPER] TARGET {symbol} @ ₹{target:.2f}")
        return "PAPER_TGT"
    resp = client.place_order(symbol=symbol, quantity=shares,
                              transaction_type="SELL", order_type="LIMIT", price=target)
    return resp.get("order_id", "UNKNOWN")


def execute_signals(signals: list, sentiment: dict):
    if not signals:
        print("No signals to execute today.")
        notifier.notify_daily_summary([], pt.load_positions())
        return

    decision        = sentiment["decision"]
    size_multiplier = sentiment["size_multiplier"]
    verdict         = sentiment["verdict"]

    # Skip all trades if market is bearish
    if decision == "SKIP":
        msg = f"Market sentiment check FAILED — {verdict}\n\nNo trades placed today.\n\nChecks:\n"
        for c in sentiment["checks"]:
            msg += f"  {c['message']}\n"
        print(msg)
        notifier.send_email(
            subject=f"[BOT] No Trades Today — {datetime.now().strftime('%Y-%m-%d')}",
            body=msg
        )
        return

    if decision == "REDUCE":
        print(f"⚠️ Weak market — trading with {size_multiplier*100:.0f}% position size")

    client         = GrowwClient()
    open_positions = pt.load_positions()
    open_symbols   = {p["symbol"] for p in open_positions}
    executed       = []

    for sig in signals:
        symbol = sig["symbol"]
        shares = max(1, int(int(sig.get("shares", 0)) * size_multiplier))

        if symbol in open_symbols:
            print(f"Already holding {symbol} — skipping")
            continue

        if len(open_positions) + len(executed) >= config.MAX_OPEN_POSITIONS:
            print(f"Max positions reached — skipping {symbol}")
            break

        if shares <= 0:
            continue

        entry  = float(sig["entry"])
        stop   = float(sig["stop"])
        target = float(sig["target"])

        # Check individual stock gap
        try:
            quote       = client.get_quotes([symbol])
            current_open = float(quote.get(symbol, {}).get("open", entry))
            gap_pct      = (current_open - entry) / entry * 100
            if gap_pct < -2.0:
                print(f"❌ {symbol} gapped down {gap_pct:.1f}% — skipping")
                continue
            elif gap_pct < 0:
                print(f"⚠️ {symbol} slight gap down {gap_pct:.1f}% — entering cautiously")
        except:
            pass  # if quote fails, proceed anyway

        try:
            order_id = place_buy_order(client, sig, size_multiplier)
            time.sleep(0.5)
            place_stop_loss_order(client, symbol, shares, stop)
            time.sleep(0.5)
            place_target_order(client, symbol, shares, target)
            time.sleep(0.5)

            pt.add_position(symbol=symbol, entry=entry, stop=stop,
                           target=target, shares=shares,
                           strategy=sig.get("strategy", ""), order_id=order_id)

            notifier.notify_order_placed(symbol=symbol, entry=entry, stop=stop,
                                        target=target, shares=shares,
                                        strategy=sig.get("strategy", ""))
            executed.append(sig)
            print(f"✅ {symbol} — order placed")

        except Exception as e:
            error_msg = f"Failed to place order for {symbol}: {e}"
            print(f"❌ {error_msg}")
            notifier.notify_error(error_msg)

    all_positions = pt.load_positions()
    notifier.notify_daily_summary(executed, all_positions)
    print(f"\nDone. {len(executed)} orders placed. {len(all_positions)} total open positions.")


def check_positions():
    positions = pt.load_positions()
    if not positions:
        return
    client  = GrowwClient()
    symbols = [p["symbol"] for p in positions]
    quotes  = client.get_quotes(symbols)

    for pos in positions:
        symbol = pos["symbol"]
        quote  = quotes.get(symbol, {})
        if not quote or "error" in quote:
            continue
        ltp = float(quote.get("ltp", 0) or quote.get("close", 0))
        if not ltp:
            continue
        if ltp <= pos["stop"]:
            pt.remove_position(symbol, pos["stop"], "stop_hit")
            notifier.notify_exit(symbol, pos["entry"], pos["stop"], pos["shares"], "Stop Loss Hit")
        elif ltp >= pos["target"]:
            pt.remove_position(symbol, pos["target"], "target_hit")
            notifier.notify_exit(symbol, pos["entry"], pos["target"], pos["shares"], "Target Hit")


def run_morning_session():
    print(f"\n{'='*60}")
    print(f"SWING BOT — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Mode: {'PAPER TRADE' if config.PAPER_TRADE else '⚠️ LIVE TRADE'}")
    print(f"{'='*60}\n")

    # Step 1 — Market sentiment check
    sentiment = run_sentiment_check()

    # Step 2 — Load signals
    signals = load_latest_signals()
    print(f"\nLoaded {len(signals)} signals from last scan")

    # Step 3 — Place orders
    execute_signals(signals, sentiment)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test",      action="store_true", help="Test email only")
    parser.add_argument("--check",     action="store_true", help="Check open positions")
    parser.add_argument("--positions", action="store_true", help="Print open positions")
    args = parser.parse_args()

    if args.test:
        print("Sending test email...")
        notifier.test_email()
    elif args.check:
        print("Checking positions...")
        check_positions()
    elif args.positions:
        pt.print_positions()
    else:
        run_morning_session()
