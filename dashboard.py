"""
SwingBot Dashboard — mobile-first web dashboard.
Reads live data from Google Sheets and NSE API.
"""
import os
import json
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, jsonify

app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), 'templates'))

IST = timezone(timedelta(hours=5, minutes=30))

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

_nse_session = None

def get_nse_session():
    global _nse_session
    if _nse_session is None:
        _nse_session = requests.Session()
        _nse_session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
    return _nse_session


def get_live_price(symbol: str) -> float:
    """Fetch live LTP from NSE for a stock symbol."""
    try:
        s = get_nse_session()
        r = s.get(f"https://www.nseindia.com/api/quote-equity?symbol={symbol}",
                  headers=NSE_HEADERS, timeout=10)
        price_info = r.json().get("priceInfo", {})
        return float(price_info.get("lastPrice", 0) or price_info.get("close", 0) or 0)
    except Exception:
        return 0.0


def get_workbook():
    from google.oauth2.service_account import Credentials
    import gspread

    creds_json = os.environ.get("GOOGLE_CREDS_JSON")
    sheet_id   = os.environ.get("GOOGLE_SHEET_ID")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    if creds_json:
        creds_dict = json.loads(creds_json)
        creds      = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    else:
        creds_file = os.path.join(os.path.dirname(__file__), "data", "swingbot-credentials.json")
        creds      = Credentials.from_service_account_file(creds_file, scopes=scopes)

    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id)


def get_sheet_data(tab_name):
    try:
        wb    = get_workbook()
        sheet = wb.worksheet(tab_name)
        return sheet.get_all_records()
    except Exception as e:
        print(f"Sheet load failed [{tab_name}]: {e}")
        return []


def get_sentiment():
    try:
        s    = get_nse_session()
        r    = s.get("https://www.nseindia.com/api/allIndices", headers=NSE_HEADERS, timeout=10)
        data = r.json().get("data", [])

        vix_data   = next((x for x in data if "VIX"      in str(x.get("index", "")).upper()), {})
        nifty_data = next((x for x in data if "NIFTY 50" == str(x.get("index", "")).upper()), {})

        vix       = float(vix_data.get("last", 0) or 0)
        nifty_chg = float(nifty_data.get("percentChange", 0) or 0)
        nifty_last= float(nifty_data.get("last", 0) or 0)
        advances  = int(nifty_data.get("advances", 0) or 0)
        declines  = int(nifty_data.get("declines", 0) or 0)
        ad_ratio  = round(advances / declines, 2) if declines else 0

        score = 0
        if vix > 0:       score += 1 if vix < 18 else (0 if vix < 22 else -2)
        if nifty_chg != 0: score += 1 if nifty_chg >= -0.3 else (0 if nifty_chg >= -0.8 else -2)
        if ad_ratio > 0:  score += 1 if ad_ratio >= 1.5 else (0 if ad_ratio >= 0.8 else -2)

        decision = "TRADE" if score >= 3 else ("SKIP" if score < 0 else "REDUCE")

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
    positions = get_sheet_data("Positions_DB")
    signals   = get_sheet_data("Signals")
    trade_log = get_sheet_data("Trade Log")
    daily_pnl = get_sheet_data("Daily P&L")
    sentiment = get_sentiment()

    # Enrich positions with live prices from NSE
    total_invested  = 0
    total_live_pnl  = 0
    enriched_positions = []

    for p in positions:
        if not p.get("symbol"):
            continue
        entry  = float(p.get("entry", 0) or 0)
        stop   = float(p.get("stop", 0) or 0)
        target = float(p.get("target", 0) or 0)
        shares = int(p.get("shares", 0) or 0)

        ltp    = get_live_price(p["symbol"])
        pnl    = round((ltp - entry) * shares, 2) if ltp else 0
        pnl_pct= round((ltp - entry) / entry * 100, 2) if ltp and entry else 0
        invested = round(entry * shares, 2)

        # Status
        if ltp and ltp >= target:   status = "TARGET"
        elif ltp and ltp <= stop:   status = "STOP"
        elif pnl > 0:               status = "PROFIT"
        elif pnl < 0:               status = "LOSS"
        else:                       status = "HOLDING"

        total_invested += invested
        total_live_pnl += pnl

        enriched_positions.append({
            **p,
            "ltp":     round(ltp, 2),
            "pnl":     pnl,
            "pnl_pct": pnl_pct,
            "invested": invested,
            "status":  status,
        })

    wins     = len([t for t in trade_log if float(t.get("pnl", 0)) > 0])
    win_rate = round(wins / len(trade_log) * 100, 1) if trade_log else 0
    closed_pnl = sum(float(t.get("pnl", 0)) for t in trade_log)

    return jsonify({
        "positions":  enriched_positions,
        "signals":    signals,
        "trade_log":  trade_log[-20:],
        "daily_pnl":  daily_pnl[-30:],
        "sentiment":  sentiment,
        "summary": {
            "open_positions": len(enriched_positions),
            "total_invested": round(total_invested, 0),
            "live_pnl":       round(total_live_pnl, 0),
            "closed_pnl":     round(closed_pnl, 0),
            "win_rate":       win_rate,
            "total_trades":   len(trade_log),
        },
        "last_updated": datetime.now(IST).strftime("%H:%M:%S IST"),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)