"""
Position tracker — saves and loads open positions to disk so the bot
remembers what it owns between sessions.
"""
import json
import os
from datetime import datetime

import config

POSITIONS_FILE = os.path.join(config.DATA_DIR, "open_positions.json")
TRADE_LOG_FILE = os.path.join(config.DATA_DIR, "live_trade_log.json")


def load_positions() -> list:
    """Load open positions from disk."""
    if not os.path.exists(POSITIONS_FILE):
        return []
    with open(POSITIONS_FILE) as f:
        return json.load(f)


def save_positions(positions: list):
    """Save open positions to disk."""
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2, default=str)


def add_position(symbol: str, entry: float, stop: float,
                 target: float, shares: int, strategy: str,
                 order_id: str = "PAPER"):
    positions = load_positions()
    # Avoid duplicates
    if any(p["symbol"] == symbol for p in positions):
        print(f"Position already exists for {symbol} — skipping")
        return
    positions.append({
        "symbol": symbol,
        "entry": entry,
        "stop": stop,
        "target": target,
        "shares": shares,
        "strategy": strategy,
        "order_id": order_id,
        "entry_date": datetime.now().strftime("%Y-%m-%d"),
        "entry_time": datetime.now().strftime("%H:%M:%S"),
    })
    save_positions(positions)
    print(f"Position added: {symbol} — {shares} shares @ ₹{entry:.2f}")


def remove_position(symbol: str, exit_price: float, reason: str):
    positions = load_positions()
    pos = next((p for p in positions if p["symbol"] == symbol), None)
    if not pos:
        print(f"No open position found for {symbol}")
        return None

    pnl = (exit_price - pos["entry"]) * pos["shares"]

    # Log the closed trade
    log_trade({
        **pos,
        "exit_price": exit_price,
        "exit_date": datetime.now().strftime("%Y-%m-%d"),
        "exit_reason": reason,
        "pnl": pnl,
    })

    positions = [p for p in positions if p["symbol"] != symbol]
    save_positions(positions)
    print(f"Position closed: {symbol} | P&L: ₹{pnl:,.0f} | Reason: {reason}")
    return pnl


def log_trade(trade: dict):
    trades = []
    if os.path.exists(TRADE_LOG_FILE):
        with open(TRADE_LOG_FILE) as f:
            trades = json.load(f)
    trades.append(trade)
    with open(TRADE_LOG_FILE, "w") as f:
        json.dump(trades, f, indent=2, default=str)


def print_positions():
    positions = load_positions()
    if not positions:
        print("No open positions.")
        return
    print(f"\nOpen Positions ({len(positions)}):")
    print("-" * 80)
    for p in positions:
        print(f"{p['symbol']:12s} | {p['shares']} shares @ ₹{p['entry']:.2f} | "
              f"Stop: ₹{p['stop']:.2f} | Target: ₹{p['target']:.2f} | "
              f"Since: {p['entry_date']}")
