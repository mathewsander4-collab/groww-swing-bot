"""
Market sentiment filter — checks market conditions before placing any trades.
Uses Groww API (listIndices) for VIX and Nifty data.

Scoring system:
  Green   = +1
  Caution =  0
  Red     = -2

Total score:
  +3 to +5  → TRADE NORMALLY
   0 to +2  → TRADE with 50% position size
  -1 or below → SKIP ALL TRADES
"""
import os
from datetime import datetime

import config

# Thresholds
VIX_GREEN    = 18.0
VIX_RED      = 22.0
NIFTY_GREEN  = -0.3
NIFTY_RED    = -0.8
FII_RED      = -2000
AD_GREEN     = 1.5
AD_RED       = 0.8
GAP_DOWN_RED = -2.0


def get_indices() -> list:
    """Fetch all indices from Groww API."""
    try:
        from groww_client import GrowwClient
        client = GrowwClient()
        return client.listIndices() or []
    except Exception as e:
        print(f"[SENTIMENT] listIndices failed: {e}")
        return []


def find_index(indices: list, name: str) -> dict:
    """Find an index by partial name match."""
    name_upper = name.upper()
    for idx in indices:
        idx_name = str(idx.get("indexName", "") or idx.get("index", "") or idx.get("name", "")).upper()
        if name_upper in idx_name:
            return idx
    return {}


def check_vix(indices: list) -> tuple:
    """Check India VIX fear index."""
    try:
        idx = find_index(indices, "VIX")
        if not idx:
            return 0, "VIX: Not found in indices — skipping check"

        vix = float(idx.get("last", 0) or idx.get("lastPrice", 0) or idx.get("close", 0))
        if vix == 0:
            return 0, "VIX: Could not read value — skipping check"

        if vix < VIX_GREEN:
            return 1, f"VIX: {vix:.1f} ✅ Calm market"
        elif vix < VIX_RED:
            return 0, f"VIX: {vix:.1f} ⚠️ Elevated — caution"
        else:
            return -2, f"VIX: {vix:.1f} ❌ High fear — skip trades"
    except Exception as e:
        return 0, f"VIX: Error ({e}) — skipping check"


def check_nifty(indices: list) -> tuple:
    """Check Nifty 50 trend."""
    try:
        idx = find_index(indices, "NIFTY 50")
        if not idx:
            idx = find_index(indices, "NIFTY50")
        if not idx:
            return 0, "Nifty: Not found in indices — skipping check"

        change_pct = float(idx.get("percentChange", 0) or idx.get("pChange", 0) or 0)

        if change_pct >= NIFTY_GREEN:
            return 1, f"Nifty: {change_pct:+.2f}% ✅ Positive/flat"
        elif change_pct >= NIFTY_RED:
            return 0, f"Nifty: {change_pct:+.2f}% ⚠️ Mild weakness — caution"
        else:
            return -2, f"Nifty: {change_pct:+.2f}% ❌ Significant drop — skip trades"
    except Exception as e:
        return 0, f"Nifty: Error ({e}) — skipping check"


def check_fii(indices: list) -> tuple:
    """FII data not available via Groww indices — skip with neutral score."""
    return 0, "FII: Not available via Groww API — skipping check"


def check_advance_decline(indices: list) -> tuple:
    """Estimate A/D ratio from index breadth if available, else skip."""
    try:
        # Some brokers return advances/declines in index data
        nifty500 = find_index(indices, "NIFTY 500")
        if not nifty500:
            return 0, "A/D: Data not available — skipping check"

        advances = float(nifty500.get("advances", 0) or 0)
        declines = float(nifty500.get("declines", 0) or 0)

        if advances == 0 or declines == 0:
            return 0, "A/D: Advance/decline counts not in response — skipping check"

        ratio = advances / declines

        if ratio >= AD_GREEN:
            return 1, f"A/D Ratio: {ratio:.2f} ✅ More stocks rising ({int(advances)} up / {int(declines)} down)"
        elif ratio >= AD_RED:
            return 0, f"A/D Ratio: {ratio:.2f} ⚠️ Mixed market ({int(advances)} up / {int(declines)} down)"
        else:
            return -2, f"A/D Ratio: {ratio:.2f} ❌ Most stocks falling ({int(advances)} up / {int(declines)} down)"
    except Exception as e:
        return 0, f"A/D: Error ({e}) — skipping check"


def check_stock_gap(symbol: str, entry_price: float, current_open: float) -> tuple:
    """Check if individual stock has gapped down significantly."""
    if current_open <= 0:
        return 0, f"{symbol}: Gap unknown"

    gap_pct = (current_open - entry_price) / entry_price * 100

    if gap_pct >= 1.0:
        return 1, f"{symbol}: Gap up {gap_pct:+.1f}% ✅ Strong open"
    elif gap_pct >= GAP_DOWN_RED:
        return 0, f"{symbol}: Gap {gap_pct:+.1f}% ⚠️ Slight weakness"
    else:
        return -2, f"{symbol}: Gap down {gap_pct:+.1f}% ❌ Skip this stock"


class MarketSentiment:
    def __init__(self):
        self.score = 0
        self.checks = []
        self.decision = "TRADE"
        self.size_multiplier = 1.0

    def run_all_checks(self) -> dict:
        print("\n📊 Running market sentiment checks...")
        print("─" * 50)

        indices = get_indices()
        if not indices:
            print("  ⚠️  Could not fetch indices from Groww — defaulting to REDUCE size")
            return {
                "score": 0, "decision": "REDUCE", "size_multiplier": 0.5,
                "verdict": "⚠️ TRADE WITH 50% POSITION SIZE (indices unavailable)",
                "checks": [],
            }

        checks = [
            ("VIX",       check_vix(indices)),
            ("Nifty",     check_nifty(indices)),
            ("FII",       check_fii(indices)),
            ("A/D Ratio", check_advance_decline(indices)),
        ]

        total_score = 0
        for name, (score, message) in checks:
            total_score += score
            self.checks.append({"name": name, "score": score, "message": message})
            print(f"  {message}")

        self.score = total_score

        if total_score >= 3:
            self.decision = "TRADE"
            self.size_multiplier = 1.0
            verdict = "✅ TRADE NORMALLY"
        elif total_score >= 0:
            self.decision = "REDUCE"
            self.size_multiplier = 0.5
            verdict = "⚠️ TRADE WITH 50% POSITION SIZE"
        else:
            self.decision = "SKIP"
            self.size_multiplier = 0.0
            verdict = "❌ SKIP ALL TRADES TODAY"

        print("─" * 50)
        print(f"  Total Score: {total_score}")
        print(f"  Decision:    {verdict}")
        print("─" * 50)

        return {
            "score":           total_score,
            "decision":        self.decision,
            "size_multiplier": self.size_multiplier,
            "verdict":         verdict,
            "checks":          self.checks,
        }

    def check_individual_stock(self, symbol: str, entry: float, current_open: float) -> tuple:
        score, message = check_stock_gap(symbol, entry, current_open)
        return score >= 0, message


def run_sentiment_check() -> dict:
    sentiment = MarketSentiment()
    return sentiment.run_all_checks()


if __name__ == "__main__":
    result = run_sentiment_check()
    print(f"\nFinal decision: {result['verdict']}")
