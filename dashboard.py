"""
Live dashboard — shows open positions, current P&L, and today's signals.
Runs a local web server accessible from your phone on the same hotspot.

Usage:
    python dashboard.py
Then open on phone browser: http://192.168.x.x:5000
(IP address will be printed when you run it)
"""
import json
import os
import socket
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import config
import position_tracker as pt
from groww_client import GrowwClient

# Cache prices so we don't hit API on every page refresh
_price_cache = {}
_cache_time = 0
CACHE_TTL = 60  # refresh prices every 60 seconds


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


def fetch_prices(symbols: list) -> dict:
    global _price_cache, _cache_time
    now = time.time()
    if now - _cache_time < CACHE_TTL and _price_cache:
        return _price_cache
    try:
        client = GrowwClient()
        quotes = client.get_quotes(symbols)
        prices = {}
        for sym, q in quotes.items():
            if isinstance(q, dict) and "error" not in q:
                prices[sym] = float(q.get("ltp") or q.get("close") or 0)
        _price_cache = prices
        _cache_time = now
        return prices
    except Exception as e:
        print(f"Price fetch error: {e}")
        return _price_cache


def load_latest_signals() -> list:
    import glob
    pattern = os.path.join(config.DATA_DIR, "scan_*.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        return []
    import pandas as pd
    df = pd.read_csv(files[-1])
    return df.to_dict("records")


def build_html() -> str:
    positions = pt.load_positions()
    signals = load_latest_signals()

    # Fetch current prices
    symbols = [p["symbol"] for p in positions]
    prices = fetch_prices(symbols) if symbols else {}

    # Calculate P&L
    total_invested = 0
    total_current = 0
    total_pnl = 0
    rows = []

    for p in positions:
        sym = p["symbol"]
        entry = p["entry"]
        shares = p["shares"]
        stop = p["stop"]
        target = p["target"]
        ltp = prices.get(sym, 0)
        invested = entry * shares
        current_val = ltp * shares if ltp else invested
        pnl = (ltp - entry) * shares if ltp else 0
        pnl_pct = ((ltp - entry) / entry * 100) if ltp else 0

        total_invested += invested
        total_current += current_val
        total_pnl += pnl

        # Status
        if ltp >= target:
            status = "🎯 TARGET"
            status_color = "#00c853"
        elif ltp <= stop and ltp > 0:
            status = "🛑 STOP"
            status_color = "#ff1744"
        elif pnl > 0:
            status = "🟢 PROFIT"
            status_color = "#00c853"
        elif pnl < 0:
            status = "🔴 LOSS"
            status_color = "#ff1744"
        else:
            status = "⚪ WAITING"
            status_color = "#888"

        rows.append({
            "symbol": sym,
            "entry": entry,
            "ltp": ltp,
            "stop": stop,
            "target": target,
            "shares": shares,
            "invested": invested,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "status": status,
            "status_color": status_color,
            "since": p.get("entry_date", ""),
            "strategy": p.get("strategy", ""),
        })

    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested else 0
    mode = "📄 PAPER TRADE" if config.PAPER_TRADE else "💰 LIVE TRADE"
    pnl_color = "#00c853" if total_pnl >= 0 else "#ff1744"

    # Build position rows HTML
    position_rows_html = ""
    for r in rows:
        pnl_str = f"₹{r['pnl']:+,.0f} ({r['pnl_pct']:+.1f}%)" if r["ltp"] else "—"
        ltp_str = f"₹{r['ltp']:.2f}" if r["ltp"] else "—"
        pnl_cell_color = "#00c853" if r["pnl"] >= 0 else "#ff1744"
        position_rows_html += f"""
        <tr>
            <td><b>{r['symbol']}</b><br><small style='color:#aaa'>{r['strategy']}</small></td>
            <td>₹{r['entry']:.2f}</td>
            <td>{ltp_str}</td>
            <td>₹{r['stop']:.2f}</td>
            <td>₹{r['target']:.2f}</td>
            <td>{r['shares']}</td>
            <td>₹{r['invested']:,.0f}</td>
            <td style='color:{pnl_cell_color};font-weight:bold'>{pnl_str}</td>
            <td style='color:{r['status_color']};font-weight:bold'>{r['status']}</td>
            <td><small>{r['since']}</small></td>
        </tr>"""

    # Build signals HTML
    signal_rows_html = ""
    for s in signals[:8]:
        signal_rows_html += f"""
        <tr>
            <td><b>{s['symbol']}</b></td>
            <td>{s['strategy']}</td>
            <td>₹{float(s['entry']):.2f}</td>
            <td>₹{float(s['stop']):.2f}</td>
            <td>₹{float(s['target']):.2f}</td>
            <td>{int(s['shares'])}</td>
            <td>₹{float(s['capital_used']):,.0f}</td>
            <td>{s['reason']}</td>
        </tr>"""

    # Available capital
    used_capital = sum(p["entry"] * p["shares"] for p in positions)
    available = max(0, config.CAPITAL - used_capital)

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset='utf-8'>
    <meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>Swing Bot Dashboard</title>
    <meta http-equiv='refresh' content='60'>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ background: #0d0d0d; color: #f0f0f0; font-family: Arial, sans-serif; padding: 12px; }}
        h1 {{ font-size: 20px; margin-bottom: 4px; }}
        h2 {{ font-size: 16px; margin: 16px 0 8px; color: #aaa; }}
        .badge {{ display:inline-block; padding:3px 10px; border-radius:12px; font-size:12px; background:#333; margin-left:8px; }}
        .cards {{ display:flex; flex-wrap:wrap; gap:10px; margin:12px 0; }}
        .card {{ background:#1a1a1a; border-radius:10px; padding:14px 18px; min-width:140px; flex:1; }}
        .card .label {{ font-size:11px; color:#888; margin-bottom:4px; }}
        .card .value {{ font-size:22px; font-weight:bold; }}
        table {{ width:100%; border-collapse:collapse; font-size:12px; }}
        th {{ background:#1a1a1a; padding:8px 6px; text-align:left; color:#aaa; font-weight:normal; }}
        td {{ padding:8px 6px; border-bottom:1px solid #1a1a1a; vertical-align:top; }}
        tr:hover td {{ background:#1a1a1a; }}
        .time {{ font-size:11px; color:#555; margin-top:4px; }}
        .scroll {{ overflow-x:auto; }}
    </style>
</head>
<body>
    <h1>📈 Swing Bot <span class='badge'>{mode}</span></h1>
    <div class='time'>Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} &nbsp;|&nbsp; Auto-refresh: 60s</div>

    <div class='cards'>
        <div class='card'>
            <div class='label'>Total Capital</div>
            <div class='value'>₹{config.CAPITAL:,.0f}</div>
        </div>
        <div class='card'>
            <div class='label'>Invested</div>
            <div class='value'>₹{used_capital:,.0f}</div>
        </div>
        <div class='card'>
            <div class='label'>Available</div>
            <div class='value'>₹{available:,.0f}</div>
        </div>
        <div class='card'>
            <div class='label'>Total P&L</div>
            <div class='value' style='color:{pnl_color}'>₹{total_pnl:+,.0f}</div>
        </div>
        <div class='card'>
            <div class='label'>P&L %</div>
            <div class='value' style='color:{pnl_color}'>{total_pnl_pct:+.2f}%</div>
        </div>
        <div class='card'>
            <div class='label'>Open Trades</div>
            <div class='value'>{len(positions)}</div>
        </div>
    </div>

    <h2>📂 Open Positions</h2>
    <div class='scroll'>
    <table>
        <tr>
            <th>Stock</th><th>Entry</th><th>LTP</th><th>Stop</th>
            <th>Target</th><th>Qty</th><th>Invested</th><th>P&L</th>
            <th>Status</th><th>Since</th>
        </tr>
        {position_rows_html if position_rows_html else "<tr><td colspan='10' style='text-align:center;color:#555;padding:20px'>No open positions</td></tr>"}
    </table>
    </div>

    <h2>🔍 Latest Signals</h2>
    <div class='scroll'>
    <table>
        <tr>
            <th>Stock</th><th>Strategy</th><th>Entry</th><th>Stop</th>
            <th>Target</th><th>Qty</th><th>Capital</th><th>Reason</th>
        </tr>
        {signal_rows_html if signal_rows_html else "<tr><td colspan='8' style='text-align:center;color:#555;padding:20px'>No signals — run python main.py scan</td></tr>"}
    </table>
    </div>
</body>
</html>"""
    return html


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        html = build_html()
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, format, *args):
        pass  # suppress request logs


def run_server(port=5000):
    ip = get_local_ip()
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"\n{'='*50}")
    print(f"Dashboard running!")
    print(f"Open on your phone: http://{ip}:{port}")
    print(f"Make sure phone is on same hotspot as laptop")
    print(f"Page auto-refreshes every 60 seconds")
    print(f"Press Ctrl+C to stop")
    print(f"{'='*50}\n")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
