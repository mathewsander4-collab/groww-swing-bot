"""
Position tracker — saves and loads open positions to Google Sheets
so positions persist across Railway restarts and redeploys.

Falls back to local JSON if Sheets is unavailable.
"""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import config

IST = ZoneInfo("Asia/Kolkata")


def ist_now() -> datetime:
    """Current time in IST, regardless of server timezone (Railway runs UTC)."""
    return datetime.now(IST)


POSITIONS_FILE = os.path.join(config.DATA_DIR, "open_positions.json")
TRADE_LOG_FILE = os.path.join(config.DATA_DIR, "live_trade_log.json")

POSITIONS_HEADERS = [
    "symbol", "entry", "stop", "initial_stop", "target", "shares",
    "strategy", "order_id", "entry_date", "entry_time"
]


# ── Google Sheets helpers ─────────────────────────────────────────────────────

def _get_positions_sheet():
    """Get the Positions sheet tab."""
    from google.oauth2.service_account import Credentials
    import gspread

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds    = Credentials.from_service_account_file(config.GOOGLE_CREDS_FILE, scopes=scopes)
    client   = gspread.authorize(creds)
    workbook = client.open_by_key(config.GOOGLE_SHEET_ID)

    try:
        sheet = workbook.worksheet("Positions_DB")
    except Exception:
        sheet = workbook.add_worksheet(title="Positions_DB", rows=200, cols=20)
        sheet.append_row(POSITIONS_HEADERS)

    return sheet


def _sheet_to_positions(sheet) -> list:
    """Read all rows from Positions_DB sheet into list of dicts."""
    rows = sheet.get_all_records()
    positions = []
    for row in rows:
        if not row.get("symbol"):
            continue
        try:
            # initial_stop is the ORIGINAL stop set at entry — used for R-multiple
            # math and must never be overwritten by trailing logic. Legacy rows
            # created before this field existed won't have it: leave as None
            # rather than guessing, so exit_manager.py can decide how to handle
            # "unknown" explicitly instead of silently freezing on a fabricated
            # value.
            raw_initial_stop = row.get("initial_stop", "")
            initial_stop = float(raw_initial_stop) if str(raw_initial_stop).strip() else None

            positions.append({
                "symbol":       str(row["symbol"]),
                "entry":        float(row["entry"]),
                "stop":         float(row["stop"]),
                "initial_stop": initial_stop,
                "target":       float(row["target"]),
                "shares":       int(row["shares"]),
                "strategy":     str(row.get("strategy", "")),
                "order_id":     str(row.get("order_id", "PAPER")),
                "entry_date":   str(row.get("entry_date", "")),
                "entry_time":   str(row.get("entry_time", "")),
            })
        except Exception as e:
            print(f"[PT] Skipping bad row {row}: {e}")
    return positions


def _positions_to_sheet(sheet, positions: list):
    """Write all positions to sheet (clears first)."""
    sheet.clear()
    sheet.append_row(POSITIONS_HEADERS)
    for p in positions:
        initial_stop = p.get("initial_stop")
        sheet.append_row([
            p["symbol"], p["entry"], p["stop"],
            initial_stop if initial_stop is not None else "",
            p["target"], p["shares"],
            p.get("strategy", ""), p.get("order_id", "PAPER"),
            p.get("entry_date", ""), p.get("entry_time", "")
        ])


# ── Public API ────────────────────────────────────────────────────────────────

def load_positions() -> list:
    """Load open positions from Google Sheets (fallback: local JSON)."""
    try:
        sheet = _get_positions_sheet()
        positions = _sheet_to_positions(sheet)
        # Also keep local copy as backup
        _save_local(positions)
        return positions
    except Exception as e:
        print(f"[PT] Sheets load failed ({e}) — using local JSON")
        return _load_local()


def save_positions(positions: list):
    """Save open positions to Google Sheets (and local JSON backup)."""
    try:
        sheet = _get_positions_sheet()
        _positions_to_sheet(sheet, positions)
        _save_local(positions)
    except Exception as e:
        print(f"[PT] Sheets save failed ({e}) — saving to local JSON only")
        _save_local(positions)


def add_position(symbol: str, entry: float, stop: float,
                 target: float, shares: int, strategy: str,
                 order_id: str = "PAPER"):
    positions = load_positions()
    if any(p["symbol"] == symbol for p in positions):
        print(f"Position already exists for {symbol} — skipping")
        return
    positions.append({
        "symbol":       symbol,
        "entry":        entry,
        "stop":         stop,
        "initial_stop": stop,   # fixed anchor for R-multiple math — never changes
        "target":       target,
        "shares":       shares,
        "strategy":     strategy,
        "order_id":     order_id,
        "entry_date":   ist_now().strftime("%Y-%m-%d"),
        "entry_time":   ist_now().strftime("%H:%M:%S"),
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

    log_trade({
        **pos,
        "exit_price":  exit_price,
        "exit_date":   ist_now().strftime("%Y-%m-%d"),
        "exit_reason": reason,
        "pnl":         pnl,
    })

    positions = [p for p in positions if p["symbol"] != symbol]
    save_positions(positions)
    print(f"Position closed: {symbol} | P&L: ₹{pnl:,.0f} | Reason: {reason}")
    return pnl


def log_trade(trade: dict):
    """Append closed trade to local JSON and Google Sheets Trade Log tab."""
    # 1. Save to local JSON
    trades = []
    if os.path.exists(TRADE_LOG_FILE):
        with open(TRADE_LOG_FILE) as f:
            trades = json.load(f)
    trades.append(trade)
    with open(TRADE_LOG_FILE, "w") as f:
        json.dump(trades, f, indent=2, default=str)

    # 2. Append to Sheets Trade Log tab (so it survives Railway redeploys)
    try:
        sheet = _get_positions_sheet()
        workbook = sheet.spreadsheet
        try:
            log_sheet = workbook.worksheet("Trade Log")
        except Exception:
            log_sheet = workbook.add_worksheet(title="Trade Log", rows=1000, cols=15)
            log_sheet.append_row([
                "symbol", "strategy", "entry", "exit_price", "shares",
                "entry_date", "exit_date", "exit_reason", "pnl"
            ])
        log_sheet.append_row([
            trade.get("symbol", ""),
            trade.get("strategy", ""),
            trade.get("entry", 0),
            trade.get("exit_price", 0),
            trade.get("shares", 0),
            trade.get("entry_date", ""),
            trade.get("exit_date", ""),
            trade.get("exit_reason", ""),
            round(trade.get("pnl", 0), 2),
        ])
        print(f"[PT] Trade logged to Sheets: {trade.get('symbol')} P&L ₹{trade.get('pnl', 0):+,.0f}")
    except Exception as e:
        print(f"[PT] Sheets trade log failed ({e}) — local JSON saved")


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


# ── Local JSON fallback ───────────────────────────────────────────────────────

def _load_local() -> list:
    if not os.path.exists(POSITIONS_FILE):
        return []
    with open(POSITIONS_FILE) as f:
        return json.load(f)


def _save_local(positions: list):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2, default=str)