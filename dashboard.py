"""
SwingBot Dashboard — mobile-first web dashboard.
"""
import os
import json
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, jsonify

app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), 'templates'))

IST = timezone(timedelta(hours=5, minutes=30))


def get_workbook():
    from google.oauth2.service_account import Credentials
    import gspread

    creds_json = os.environ.get("GOOGLE_CREDS_JSON")
    sheet_id   = os.environ.get("GOOGLE_SHEET_ID")
    scopes     = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    if creds_json:
        creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=scopes)
    else:
        creds_file = os.path.join(os.path.dirname(__file__), "data", "swingbot-credentials.json")
        creds      = Credentials.from_service_account_file(creds_file, scopes=scopes)

    return gspread.authorize(creds).open_by_key(sheet_id)


def get_sheet_data(tab_name):
    try:
        return get_workbook().worksheet(tab_name).get_all_records()
    except Exception as e:
        print(f"Sheet load failed [{tab_name}]: {e}")
        return []


def get_current_price(symbol: str) -> float:
    """Try Groww API first, fallback to yfinance."""
    try:
        from groww_client import GrowwClient
        client = GrowwClient()
        data   = client.equityQuote(symbol)
        if data:
            price = float(data.get("close", 0) or data.get("ltp", 0) or data.get("lastPrice", 0))
            if price > 0:
                return price
    except:
        pass
    try:
        import yfinance as yf
        hist = yf.Ticker(f"{symbol}.NS").history(period="2d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 2)
    except:
        pass
    return 0.0


def get_sentiment():
    try:
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Referer": "https://www.nseindia.com/",
        }
        s = requests.Session()
        s.get("https://www.nseindia.com", headers=headers, timeout=10)
        r    = s.get("https://www.nseindia.com/api/allIndices", headers=headers, timeout=10)
        data = r.json().get("data", [])

        vix_data   = next((x for x in data if "VIX"      in str(x.get("index", "")).upper()), {})
        nifty_data = next((x for x in data if "NIFTY 50" == str(x.get("index", "")).upper()), {})

        vix        = float(vix_data.get("last", 0) or 0)
        nifty_chg  = float(nifty_data.get("percentChange", 0) or 0)
        nifty_last = float(nifty_data.get("last", 0) or 0)
        advances   = int(nifty_data.get("advances", 0) or 0)
        declines   = int(nifty_data.get("declines", 0) or 0)
        ad_ratio   = round(advances / declines, 2) if declines else 0

        score = 0
        if vix > 0:
            score += 1 if vix < 18 else (0 if vix < 22 else -2)
        if nifty_chg != 0:
            score += 1 if nifty_chg >= -0.3 else (0 if nifty_chg >= -0.8 else -2)
        if ad_ratio > 0:
            score += 1 if ad_ratio >= 1.5 else (0 if ad_ratio >= 0.8 else -2)

        decision = "TRADE" if score >= 3 else ("REDUCE" if score >= 0 else "SKIP")

        return {
            "vix": vix, "nifty_chg": nifty_chg, "nifty_last": nifty_last,
            "advances": advances, "declines": declines, "ad_ratio": ad_ratio,
            "score": score, "decision": decision,
        }
    except Exception as e:
        return {"error": str(e)}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def api_data():
    positions_db = get_sheet_data("Positions_DB")
    signals      = get_sheet_data("Signals")
    trade_log    = get_sheet_data("Trade Log")
    daily_pnl    = get_sheet_data("Daily P&L")
    sentiment    = get_sentiment()

    # Enrich positions with live P&L
    positions_live = []
    total_live_pnl = 0.0

    for p in positions_db:
        if not p.get("symbol"):
            continue
        symbol  = str(p["symbol"])
        entry   = float(p.get("entry", 0))
        stop    = float(p.get("stop", 0))
        target  = float(p.get("target", 0))
        shares  = int(p.get("shares", 0))

        current = get_current_price(symbol)
        pnl     = round((current - entry) * shares, 0) if current else 0
        pnl_pct = round((current - entry) / entry * 100, 2) if current and entry else 0
        total_live_pnl += pnl

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

        positions_live.append({
            **p,
            "current":  current,
            "pnl":      pnl,
            "pnl_pct":  pnl_pct,
            "status":   status,
        })

    total_invested = sum(
        float(p.get("entry", 0)) * int(p.get("shares", 0))
        for p in positions_db if p.get("symbol")
    )
    closed_pnl = sum(float(t.get("pnl", 0)) for t in trade_log)
    wins       = len([t for t in trade_log if float(t.get("pnl", 0)) > 0])
    win_rate   = round(wins / len(trade_log) * 100, 1) if trade_log else 0

    return jsonify({
        "positions":    positions_live,
        "signals":      signals,
        "trade_log":    trade_log[-20:],
        "daily_pnl":    daily_pnl[-30:] if daily_pnl else [],
        "sentiment":    sentiment,
        "summary": {
            "open_positions":  len(positions_live),
            "total_invested":  round(total_invested, 0),
            "live_pnl":        round(total_live_pnl, 0),
            "closed_pnl":      round(closed_pnl, 0),
            "win_rate":        win_rate,
            "total_trades":    len(trade_log),
        },
        "last_updated": datetime.now(IST).strftime("%H:%M:%S IST"),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)