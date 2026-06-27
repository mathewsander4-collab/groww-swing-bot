"""
SwingBot Dashboard — mobile-first web dashboard.
Reads live data from Google Sheets and NSE API.
Deploy as a separate Railway service.

Run locally:
    python dashboard.py
"""
import os
import json
from datetime import datetime
from flask import Flask, render_template, jsonify

app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), 'templates'))

# ── Google Sheets helpers ─────────────────────────────────────────────────────

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
        creds_file = os.path.join(os.path.dirname(__file__), "..", "data", "swingbot-credentials.json")
        creds      = Credentials.from_service_account_file(creds_file, scopes=scopes)

    client   = gspread.authorize(creds)
    return client.open_by_key(sheet_id)


def get_sheet_data(tab_name):
    try:
        wb    = get_workbook()
        sheet = wb.worksheet(tab_name)
        return sheet.get_all_records()
    except Exception as e:
        print(f"Sheet load failed [{tab_name}]: {e}")
        return []


# ── Sentiment via NSE ─────────────────────────────────────────────────────────

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
        r = s.get("https://www.nseindia.com/api/allIndices", headers=headers, timeout=10)
        data = r.json().get("data", [])

        vix_data   = next((x for x in data if "VIX"     in str(x.get("index", "")).upper()), {})
        nifty_data = next((x for x in data if "NIFTY 50" == str(x.get("index", "")).upper()), {})

        vix        = float(vix_data.get("last", 0) or 0)
        nifty_chg  = float(nifty_data.get("percentChange", 0) or 0)
        nifty_last = float(nifty_data.get("last", 0) or 0)
        advances   = int(nifty_data.get("advances", 0) or 0)
        declines   = int(nifty_data.get("declines", 0) or 0)
        ad_ratio   = round(advances / declines, 2) if declines else 0

        # Score
        score = 0
        if vix > 0:
            score += 1 if vix < 18 else (0 if vix < 22 else -2)
        if nifty_chg != 0:
            score += 1 if nifty_chg >= -0.3 else (0 if nifty_chg >= -0.8 else -2)
        if ad_ratio > 0:
            score += 1 if ad_ratio >= 1.5 else (0 if ad_ratio >= 0.8 else -2)

        if score >= 3:
            decision = "TRADE"
        elif score >= 0:
            decision = "REDUCE"
        else:
            decision = "SKIP"

        return {
            "vix": vix, "nifty_chg": nifty_chg, "nifty_last": nifty_last,
            "advances": advances, "declines": declines, "ad_ratio": ad_ratio,
            "score": score, "decision": decision,
        }
    except Exception as e:
        return {"error": str(e)}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def api_data():
    positions  = get_sheet_data("Positions_DB")
    signals    = get_sheet_data("Signals")
    trade_log  = get_sheet_data("Trade Log")
    daily_pnl  = get_sheet_data("Daily P&L")
    sentiment  = get_sentiment()

    # Calculate summary stats
    total_invested = sum(
        float(p.get("entry", 0)) * int(p.get("shares", 0))
        for p in positions if p.get("symbol")
    )
    total_pnl = sum(float(t.get("pnl", 0)) for t in trade_log)
    wins      = len([t for t in trade_log if float(t.get("pnl", 0)) > 0])
    win_rate  = round(wins / len(trade_log) * 100, 1) if trade_log else 0

    return jsonify({
        "positions":       positions,
        "signals":         signals,
        "trade_log":       trade_log[-20:],  # last 20 trades
        "daily_pnl":       daily_pnl[-30:],  # last 30 days
        "sentiment":       sentiment,
        "summary": {
            "open_positions":  len(positions),
            "total_invested":  round(total_invested, 0),
            "total_pnl":       round(total_pnl, 0),
            "win_rate":        win_rate,
            "total_trades":    len(trade_log),
        },
        "last_updated": datetime.now().strftime("%H:%M:%S"),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
