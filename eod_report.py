"""
End of Day Report — fetches closing prices from Groww API (primary)
with yfinance as fallback.

Usage:
    python eod_report.py
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

import config
import position_tracker as pt
from notifier import send_email

IST = ZoneInfo("Asia/Kolkata")


def ist_now() -> datetime:
    """Current time in IST, regardless of server timezone (Railway runs UTC)."""
    return datetime.now(IST)


def fetch_price(symbol: str) -> dict:
    """Fetch EOD price using Groww historical candles via nse_price module."""
    from nse_price import fetch_nse_price
    result = fetch_nse_price(symbol)
    if "error" in result:
        print(f"Price fetch failed for {symbol}: {result['error']}")
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
    if abs(risk) > 0:
        rr_text = f"1:{reward/abs(risk):.1f}"
    else:
        rr_text = "n/a (breakeven stop)"
    notes.append(f"⚖️  Risk: ₹{risk:,.0f} | Reward: ₹{reward:,.0f} | R:R = {rr_text}")

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
    lines.append(f"END OF DAY REPORT — {ist_now().strftime('%Y-%m-%d')}")
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
        f"eod_report_{ist_now().strftime('%Y%m%d')}.txt"
    )
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nReport saved to {report_path}")
    print("Sending email...")
    send_email(
        subject=f"EOD Report — {ist_now().strftime('%Y-%m-%d')}",
        body=report
    )
    print("Done!")

    print("Syncing to Google Sheets...")
    try:
        from sheets import sync_all
        sync_all()
    except Exception as e:
        print(f"Sheets sync failed: {e}")