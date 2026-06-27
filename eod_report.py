"""
End of Day Report — fetches closing prices from Groww API (primary)
with yfinance as fallback.

Usage:
    python eod_report.py
"""
import os
from datetime import datetime

import pandas as pd

import config
import position_tracker as pt
from notifier import send_email


def fetch_groww_price(symbol: str) -> dict:
    """Fetch today's price from Groww API."""
    try:
        from groww_client import GrowwClient
        client = GrowwClient()
        data   = client.equityQuote(symbol)
        if not data:
            return {"error": "Empty response"}

        close  = float(data.get("close", 0) or data.get("ltp", 0) or data.get("lastPrice", 0))
        open_  = float(data.get("open", 0))
        high   = float(data.get("high", 0) or data.get("dayHigh", 0))
        low    = float(data.get("low", 0)  or data.get("dayLow", 0))
        volume = int(data.get("volume", 0) or data.get("totalTradedVolume", 0))
        prev_close = float(data.get("previousClose", 0) or open_)

        if close == 0:
            return {"error": "Close price is 0"}

        change     = close - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0

        return {
            "open":       round(open_, 2),
            "high":       round(high, 2),
            "low":        round(low, 2),
            "close":      round(close, 2),
            "prev_close": round(prev_close, 2),
            "change":     round(change, 2),
            "change_pct": round(change_pct, 2),
            "volume":     volume,
            "source":     "Groww API"
        }
    except Exception as e:
        return {"error": str(e)}


def fetch_yahoo_price(symbol: str) -> dict:
    """Fallback: fetch from Yahoo Finance."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(f"{symbol}.NS")
        hist   = ticker.history(period="7d")
        if hist.empty:
            return {"error": "No data from Yahoo"}

        hist.columns = [str(c).lower().strip() for c in hist.columns]
        hist = hist.dropna(subset=["close"])
        if hist.empty:
            return {"error": "All NaN from Yahoo"}

        today      = hist.iloc[-1]
        prev       = hist.iloc[-2] if len(hist) > 1 else today
        latest_dt  = hist.index[-1].date() if hasattr(hist.index[-1], "date") else hist.index[-1]
        stale      = " STALE" if latest_dt < date.today() else ""

        return {
            "open":       round(float(today["open"]), 2),
            "high":       round(float(today["high"]), 2),
            "low":        round(float(today["low"]), 2),
            "close":      round(float(today["close"]), 2),
            "prev_close": round(float(prev["close"]), 2),
            "change":     round(float(today["close"]) - float(prev["close"]), 2),
            "change_pct": round((float(today["close"]) - float(prev["close"])) / float(prev["close"]) * 100, 2),
            "volume":     int(today["volume"]),
            "source":     f"Yahoo Finance ({latest_dt}){stale}"
        }
    except Exception as e:
        return {"error": str(e)}


def fetch_price(symbol: str) -> dict:
    """Try Groww first, fallback to Yahoo Finance."""
    result = fetch_groww_price(symbol)
    if "error" in result or result.get("close", 0) == 0:
        print(f"Groww failed for {symbol} ({result.get('error','')}) — trying Yahoo Finance...")
        result = fetch_yahoo_price(symbol)
    return result


def evaluate_position(pos: dict, price_data: dict) -> dict:
    symbol   = pos["symbol"]
    entry    = pos["entry"]
    stop     = pos["stop"]
    target   = pos["target"]
    shares   = pos["shares"]
    strategy = pos.get("strategy", "")

    if not price_data or "error" in price_data:
        return {
            "symbol": symbol, "strategy": strategy,
            "entry": entry, "close": 0, "high": 0, "low": 0,
            "stop": stop, "target": target, "shares": shares,
            "pnl": 0, "pnl_pct": 0, "change_pct": 0,
            "status": f"❓ DATA ERROR: {price_data.get('error', '')}",
            "notes": ["Could not fetch price data"],
            "source": "None"
        }

    close      = price_data["close"]
    high       = price_data["high"]
    low        = price_data["low"]
    change_pct = price_data["change_pct"]
    source     = price_data.get("source", "Unknown")

    pnl     = (close - entry) * shares
    pnl_pct = (close - entry) / entry * 100
    risk    = (entry - stop) * shares
    reward  = (target - entry) * shares

    if low <= stop:
        status     = "🛑 STOP HIT"
        actual_pnl = (stop - entry) * shares
    elif high >= target:
        status     = "🎯 TARGET HIT"
        actual_pnl = (target - entry) * shares
    elif close > entry:
        status     = "🟢 IN PROFIT"
        actual_pnl = pnl
    elif close < entry:
        status     = "🔴 IN LOSS"
        actual_pnl = pnl
    else:
        status     = "⚪ FLAT"
        actual_pnl = 0

    notes = []
    if high >= target:
        notes.append("✅ Target was hit during the day — excellent!")
    elif low <= stop:
        notes.append("❌ Stop was hit — capital preserved.")
    elif close > entry and close > price_data.get("open", close):
        notes.append("✅ Closed above entry with green candle — momentum continuing.")
    elif close < entry and close < price_data.get("open", close):
        notes.append("⚠️  Closed below entry with red candle — watch closely tomorrow.")
    elif close > entry:
        notes.append("✅ Above entry — holding in profit zone.")
    else:
        notes.append("⚠️  Below entry — monitor stop level carefully.")

    pct_to_target = (target - close) / close * 100
    pct_to_stop   = (close - stop)   / close * 100
    notes.append(f"📍 To target: {pct_to_target:.1f}% | To stop: {pct_to_stop:.1f}%")
    notes.append(f"📊 Volume: {price_data.get('volume', 0):,} | Source: {source}")
    notes.append(f"📈 Day range: ₹{low:.2f} — ₹{high:.2f}")
    notes.append(f"⚖️  Risk: ₹{risk:,.0f} | Reward: ₹{reward:,.0f} | R:R = 1:{reward/abs(risk):.1f}")

    return {
        "symbol": symbol, "strategy": strategy,
        "entry": entry, "close": close, "high": high, "low": low,
        "stop": stop, "target": target, "shares": shares,
        "pnl": actual_pnl, "pnl_pct": pnl_pct, "change_pct": change_pct,
        "status": status, "notes": notes, "source": source
    }


def generate_report() -> str:
    positions = pt.load_positions()
    if not positions:
        return "No open positions to evaluate."

    print(f"Fetching EOD prices for {len(positions)} positions...")
    evaluations = []
    total_pnl   = 0

    for pos in positions:
        print(f"  {pos['symbol']}...", end=" ", flush=True)
        price_data = fetch_price(pos["symbol"])
        ev         = evaluate_position(pos, price_data)
        evaluations.append(ev)
        total_pnl += ev.get("pnl", 0)
        if ev["close"]:
            print(f"₹{ev['close']:.2f} ({ev['change_pct']:+.1f}%) [{ev.get('source','')}]")
        else:
            print("FAILED")

    evaluations.sort(key=lambda x: x.get("pnl", 0), reverse=True)

    winners = [e for e in evaluations if e["pnl"] > 0]
    losers  = [e for e in evaluations if e["pnl"] < 0]
    stopped = [e for e in evaluations if "STOP"   in e["status"]]
    targets = [e for e in evaluations if "TARGET" in e["status"]]

    lines = []
    lines.append("=" * 65)
    lines.append(f"END OF DAY REPORT — {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"Mode: {'PAPER TRADE' if config.PAPER_TRADE else 'LIVE TRADE'}")
    lines.append("=" * 65)
    lines.append(f"\n📊 SUMMARY")
    lines.append(f"  Total positions : {len(evaluations)}")
    lines.append(f"  In profit       : {len(winners)}")
    lines.append(f"  In loss         : {len(losers)}")
    lines.append(f"  Stops hit       : {len(stopped)}")
    lines.append(f"  Targets hit     : {len(targets)}")
    lines.append(f"  Total P&L       : ₹{total_pnl:+,.0f}")
    lines.append(f"  P&L %           : {total_pnl/config.CAPITAL*100:+.2f}%")

    lines.append(f"\n{'─'*65}")
    lines.append("POSITION DETAILS (best to worst)")
    lines.append(f"{'─'*65}")

    for e in evaluations:
        lines.append(f"\n{e['symbol']} ({e['strategy'].upper()})")
        lines.append(f"  Status  : {e['status']}")
        lines.append(f"  Entry   : ₹{e['entry']:.2f}  →  Close: ₹{e['close']:.2f}  ({e['change_pct']:+.1f}% today)")
        lines.append(f"  Range   : ₹{e['low']:.2f} — ₹{e['high']:.2f}")
        lines.append(f"  Stop    : ₹{e['stop']:.2f}  |  Target: ₹{e['target']:.2f}")
        lines.append(f"  Shares  : {e['shares']}  |  P&L: ₹{e['pnl']:+,.0f} ({e['pnl_pct']:+.1f}%)")
        lines.append("  Notes:")
        for note in e["notes"]:
            lines.append(f"    {note}")

    lines.append(f"\n{'='*65}")
    lines.append("OVERALL EVALUATION")
    lines.append(f"{'='*65}")
    if total_pnl > 0:
        lines.append(f"✅ Good day! Portfolio up ₹{total_pnl:,.0f}")
        if len(winners) > len(losers):
            lines.append("   More winners than losers — strategy working well.")
    elif total_pnl < 0:
        lines.append(f"⚠️  Difficult day. Portfolio down ₹{abs(total_pnl):,.0f}")
        if stopped:
            lines.append(f"   {len(stopped)} stop(s) hit — losses controlled.")
        lines.append("   Stay disciplined — one bad day doesn't define the strategy.")
    else:
        lines.append("⚪ Flat day — positions consolidating.")

    return "\n".join(lines)


if __name__ == "__main__":
    report = generate_report()
    print("\n" + report)

    report_path = os.path.join(
        config.DATA_DIR,
        f"eod_report_{datetime.now().strftime('%Y%m%d')}.txt"
    )
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nReport saved to {report_path}")
    print("Sending email...")
    send_email(
        subject=f"EOD Report — {datetime.now().strftime('%Y-%m-%d')}",
        body=report
    )
    print("Done!")

    print("Syncing to Google Sheets...")
    try:
        from sheets import sync_all
        sync_all()
    except Exception as e:
        print(f"Sheets sync failed: {e}")