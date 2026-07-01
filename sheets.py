"""
Google Sheets integration — syncs all bot data to Google Sheets
so you can view everything from your phone browser anytime.

Sheets created automatically:
  1. Positions    — open trades with live P&L
  2. Trade Log    — all closed trades history
  3. Signals      — today's scan signals
  4. Daily P&L    — day by day performance summary

Usage:
    python sheets.py                  # sync everything
    python sheets.py --positions      # sync positions only
    python sheets.py --signals        # sync signals only
    python sheets.py --test           # test connection only
"""
import argparse
import glob
import json
import os
from datetime import datetime

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

import config
import position_tracker as pt

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Sheet tab names
TAB_POSITIONS  = "Positions"
TAB_TRADE_LOG  = "Trade Log"
TAB_SIGNALS    = "Signals"
TAB_DAILY_PNL  = "Daily P&L"
TAB_SUMMARY    = "Summary"


def get_client():
    """Authenticate with Google Sheets API."""
    creds = Credentials.from_service_account_file(
        config.GOOGLE_CREDS_FILE, scopes=SCOPES
    )
    return gspread.authorize(creds)


def get_or_create_sheet(workbook, tab_name: str, headers: list):
    """Get existing sheet tab or create it with headers."""
    try:
        sheet = workbook.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        sheet = workbook.add_worksheet(title=tab_name, rows=1000, cols=20)
        sheet.append_row(headers)
        # Format header row
        sheet.format("1:1", {
            "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
            "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}
        })
    return sheet


def sync_positions(workbook, price_data: dict = None):
    """Sync open positions with current P&L to Positions sheet."""
    headers = [
        "Symbol", "Strategy", "Entry Date", "Entry ₹", "Current ₹",
        "Stop ₹", "Target ₹", "Shares", "Invested ₹",
        "P&L ₹", "P&L %", "Days Held", "Status"
    ]
    sheet = get_or_create_sheet(workbook, TAB_POSITIONS, headers)

    positions = pt.load_positions()
    if not positions:
        sheet.clear()
        sheet.append_row(headers)
        sheet.append_row(["No open positions"] + [""] * (len(headers) - 1))
        return

    # Clear and rewrite
    sheet.clear()
    sheet.append_row(headers)

    rows = []
    for pos in positions:
        symbol    = pos["symbol"]
        entry     = pos["entry"]
        stop      = pos["stop"]
        target    = pos["target"]
        shares    = pos["shares"]
        strategy  = pos.get("strategy", "")
        entry_date = pos.get("entry_date", "")

        # Calculate days held
        try:
            from datetime import date
            ed   = datetime.strptime(entry_date, "%Y-%m-%d").date()
            days = (date.today() - ed).days
        except:
            days = 0

        # Get current price
        current = price_data.get(symbol, 0) if price_data else 0
        pnl     = (current - entry) * shares if current else 0
        pnl_pct = ((current - entry) / entry * 100) if current else 0
        invested = entry * shares

        # Status
        if current >= target:
            status = "TARGET HIT"
        elif current <= stop and current > 0:
            status = "STOP HIT"
        elif current > entry:
            status = "IN PROFIT"
        elif current < entry and current > 0:
            status = "IN LOSS"
        else:
            status = "HOLDING"

        rows.append([
            symbol, strategy, entry_date,
            round(entry, 2), round(current, 2) if current else "—",
            round(stop, 2), round(target, 2),
            shares, round(invested, 0),
            round(pnl, 0) if current else "—",
            f"{pnl_pct:+.1f}%" if current else "—",
            days, status
        ])

    if rows:
        sheet.append_rows(rows)

    # Add summary row
    total_invested = sum(pos["entry"] * pos["shares"] for pos in positions)
    sheet.append_row([])
    sheet.append_row([
        "TOTAL", "", "", "", "", "", "",
        sum(pos["shares"] for pos in positions),
        round(total_invested, 0),
        "", "", "", f"{len(positions)} positions"
    ])

    print(f"✅ Positions synced ({len(positions)} open)")


def sync_trade_log(workbook):
    """Verify Trade Log sheet — no longer clears/rewrites.

    Trade Log is already kept up to date in real-time by
    position_tracker.py's log_trade(), which appends one row per closed
    trade directly to Sheets (the durable source, unlike local JSON which
    can be wiped on a Railway redeploy). This function just ensures the
    tab exists and reports current row count — it does not touch existing data.
    """
    headers = [
        "symbol", "strategy", "entry", "exit_price", "shares",
        "entry_date", "exit_date", "exit_reason", "pnl"
    ]
    sheet = get_or_create_sheet(workbook, TAB_TRADE_LOG, headers)

    existing = sheet.get_all_records()
    print(f"✅ Trade log verified ({len(existing)} trades already in Sheets — no rewrite)")


def sync_signals(workbook, df: pd.DataFrame = None):
    """Sync latest scan signals to Signals sheet.

    Pass df directly from scanner (preferred on Railway — avoids CSV filesystem loss).
    Falls back to latest local CSV file if df is None (local runs).
    """
    headers = [
        "Symbol", "Strategy", "Entry ₹", "Stop ₹", "Target ₹",
        "Shares", "Capital ₹", "Risk %", "Reason", "Scan Date"
    ]
    sheet = get_or_create_sheet(workbook, TAB_SIGNALS, headers)

    scan_date = datetime.now().strftime("%Y%m%d")

    if df is None:
        # Fallback: load latest CSV (for local runs only)
        pattern = os.path.join(config.DATA_DIR, "scan_*.csv")
        files   = sorted(glob.glob(pattern))
        if not files:
            print("No scan files found.")
            return
        latest    = files[-1]
        scan_date = os.path.basename(latest).replace("scan_", "").replace(".csv", "")
        df        = pd.read_csv(latest)

    if df is None or df.empty:
        print("No signals to sync.")
        return

    sheet.clear()
    sheet.append_row(headers)

    rows = []
    for _, row in df.iterrows():
        rows.append([
            row.get("symbol", ""),
            row.get("strategy", ""),
            round(float(row.get("entry", 0)), 2),
            round(float(row.get("stop", 0)), 2),
            round(float(row.get("target", 0)), 2),
            int(row.get("shares", 0)),
            round(float(row.get("capital_used", 0)), 0),
            round(float(row.get("risk_pct", 0)), 2),
            row.get("reason", ""),
            scan_date
        ])

    if rows:
        sheet.append_rows(rows)

    print(f"✅ Signals synced ({len(rows)} signals from {scan_date})")


def load_signals(workbook) -> list:
    """Load latest signals from Sheets Signals tab — used by trader.py on morning session."""
    try:
        sheet = workbook.worksheet(TAB_SIGNALS)
        rows  = sheet.get_all_records()
        if not rows:
            return []
        signals = []
        for row in rows:
            if not row.get("Symbol"):
                continue
            signals.append({
                "symbol":       str(row.get("Symbol", "")),
                "strategy":     str(row.get("Strategy", "")),
                "entry":        float(row.get("Entry ₹", 0) or 0),
                "stop":         float(row.get("Stop ₹", 0) or 0),
                "target":       float(row.get("Target ₹", 0) or 0),
                "shares":       int(row.get("Shares", 0) or 0),
                "capital_used": float(row.get("Capital ₹", 0) or 0),
                "risk_pct":     float(row.get("Risk %", 0) or 0),
                "reason":       str(row.get("Reason", "")),
            })
        print(f"[SHEETS] Loaded {len(signals)} signals from Sheets")
        return signals
    except Exception as e:
        print(f"[SHEETS] load_signals failed: {e}")
        return []


def sync_daily_pnl(workbook, date_str: str, total_pnl: float, positions_count: int):
    """Append today's P&L to Daily P&L sheet."""
    headers = ["Date", "Total P&L ₹", "P&L %", "Open Positions", "Mode"]
    sheet   = get_or_create_sheet(workbook, TAB_DAILY_PNL, headers)

    pnl_pct = (total_pnl / config.CAPITAL * 100)
    mode    = "PAPER" if config.PAPER_TRADE else "LIVE"

    sheet.append_row([
        date_str,
        round(total_pnl, 0),
        f"{pnl_pct:+.2f}%",
        positions_count,
        mode
    ])
    print(f"✅ Daily P&L logged ({date_str}: ₹{total_pnl:+,.0f})")


def sync_summary(workbook):
    """Update summary tab with key stats."""
    headers = ["Metric", "Value"]
    sheet   = get_or_create_sheet(workbook, TAB_SUMMARY, headers)

    positions  = pt.load_positions()
    log_file   = os.path.join(config.DATA_DIR, "live_trade_log.json")
    trades     = []
    if os.path.exists(log_file):
        with open(log_file) as f:
            trades = json.load(f)

    wins        = [t for t in trades if t.get("pnl", 0) > 0]
    total_pnl   = sum(t.get("pnl", 0) for t in trades)
    win_rate    = (len(wins) / len(trades) * 100) if trades else 0
    used_capital = sum(p["entry"] * p["shares"] for p in positions)

    sheet.clear()
    sheet.append_row(headers)
    sheet.append_rows([
        ["Last Updated",       datetime.now().strftime("%Y-%m-%d %H:%M")],
        ["Mode",               "PAPER TRADE" if config.PAPER_TRADE else "LIVE TRADE"],
        ["Capital",            f"₹{config.CAPITAL:,.0f}"],
        ["Capital Deployed",   f"₹{used_capital:,.0f}"],
        ["Capital Available",  f"₹{max(0, config.CAPITAL - used_capital):,.0f}"],
        ["Open Positions",     len(positions)],
        ["Total Trades",       len(trades)],
        ["Wins",               len(wins)],
        ["Losses",             len(trades) - len(wins)],
        ["Win Rate",           f"{win_rate:.1f}%"],
        ["Total P&L",          f"₹{total_pnl:+,.0f}"],
        ["P&L %",              f"{total_pnl/config.CAPITAL*100:+.2f}%"],
    ])
    print(f"✅ Summary synced")


def sync_all(price_data: dict = None, scan_results: pd.DataFrame = None):
    """Sync everything to Google Sheets."""
    print("\nSyncing to Google Sheets...")
    try:
        client   = get_client()
        workbook = client.open_by_key(config.GOOGLE_SHEET_ID)

        sync_positions(workbook, price_data)
        sync_trade_log(workbook)
        sync_signals(workbook, df=scan_results)  # pass df directly — no CSV needed
        sync_summary(workbook)

        # Daily P&L — today's closed-trade P&L + current open position count
        positions = pt.load_positions()
        log_file  = os.path.join(config.DATA_DIR, "live_trade_log.json")
        trades    = []
        if os.path.exists(log_file):
            with open(log_file) as f:
                trades = json.load(f)

        today_str = datetime.now().strftime("%Y-%m-%d")
        today_closed_pnl = sum(
            t.get("pnl", 0) for t in trades if t.get("exit_date") == today_str
        )
        sync_daily_pnl(workbook, today_str, today_closed_pnl, len(positions))

        sheet_url = f"https://docs.google.com/spreadsheets/d/{config.GOOGLE_SHEET_ID}"
        print(f"\n✅ All data synced to Google Sheets!")
        print(f"   View: {sheet_url}")
        return True
    except Exception as e:
        print(f"❌ Google Sheets sync failed: {e}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test",      action="store_true", help="Test connection only")
    parser.add_argument("--positions", action="store_true", help="Sync positions only")
    parser.add_argument("--signals",   action="store_true", help="Sync signals only")
    parser.add_argument("--prices",    action="store_true", help="Sync positions with live prices")
    args = parser.parse_args()

    if args.test:
        print("Testing Google Sheets connection...")
        try:
            client   = get_client()
            workbook = client.open_by_key(config.GOOGLE_SHEET_ID)
            print(f"✅ Connected to: {workbook.title}")
            print(f"   URL: https://docs.google.com/spreadsheets/d/{config.GOOGLE_SHEET_ID}")
        except Exception as e:
            print(f"❌ Connection failed: {e}")

    elif args.positions:
        client   = get_client()
        workbook = client.open_by_key(config.GOOGLE_SHEET_ID)
        sync_positions(workbook)

    elif args.signals:
        client   = get_client()
        workbook = client.open_by_key(config.GOOGLE_SHEET_ID)
        sync_signals(workbook)

    elif args.prices:
        from eod_report import fetch_price
        positions = pt.load_positions()
        prices = {}
        for pos in positions:
            print(f"Fetching {pos['symbol']}...", end=" ", flush=True)
            data = fetch_price(pos["symbol"])
            prices[pos["symbol"]] = data.get("close", 0)
            print(f"₹{prices[pos['symbol']]:.2f}")
        client   = get_client()
        workbook = client.open_by_key(config.GOOGLE_SHEET_ID)
        sync_positions(workbook, prices)
        print("✅ Positions updated with live prices!")
    else:
        sync_all()