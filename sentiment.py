"""
Market sentiment filter — checks market conditions before placing any trades.
Uses NSE India data (already installed) for VIX, Nifty, FII, A/D ratio.

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

from nse import NSE

import config

# Thresholds
VIX_GREEN    = 18.0   # below this = green
VIX_RED      = 22.0   # above this = skip
NIFTY_GREEN  = -0.3   # above this % = green
NIFTY_RED    = -0.8   # below this % = skip
FII_RED      = -2000  # crores, below this = skip
AD_GREEN     = 1.5    # above this = green
AD_RED       = 0.8    # below this = skip
GAP_DOWN_RED = -2.0   # % gap down = skip that stock


def get_nse() -> NSE:
    download_dir = os.path.join(config.DATA_DIR, "nse_downloads")
    os.makedirs(download_dir, exist_ok=True)
    return NSE(download_dir)


def check_vix(nse: NSE) -> tuple:
    """Check India VIX fear index."""
    try:
        data = nse.equityQuote("INDIAVIX")
        vix = float(data.get("close", 0) or data.get("open", 0))
        if vix == 0:
            # Try indices
            indices = nse.listIndices()
            for idx in indices:
                if "VIX" in str(idx.get("index", "")):
                    vix = float(idx.get("last", 0))
                    break

        if vix == 0:
            return 0, f"VIX: Could not fetch (skipping check)"

        if vix < VIX_GREEN:
            return 1, f"VIX: {vix:.1f} ✅ Calm market"
        elif vix < VIX_RED:
            return 0, f"VIX: {vix:.1f} ⚠️ Elevated — caution"
        else:
            return -2, f"VIX: {vix:.1f} ❌ High fear — skip trades"
    except Exception as e:
        return 0, f"VIX: Error ({e}) — skipping check"


def check_nifty(nse: NSE) -> tuple:
    """Check Nifty 500 opening trend."""
    try:
        data = nse.equityQuote("NIFTY 500")
        if not data:
            # Try Nifty 50
            indices = nse.listIndices()
            nifty = next((i for i in indices if i.get("index") == "NIFTY 50"), None)
            if nifty:
                change_pct = float(nifty.get("percentChange", 0))
            else:
                return 0, "Nifty: Could not fetch — skipping check"
        else:
            close      = float(data.get("close", 0))
            open_      = float(data.get("open", 0))
            change_pct = ((close - open_) / open_ * 100) if open_ else 0

        if change_pct >= NIFTY_GREEN:
            return 1, f"Nifty: {change_pct:+.2f}% ✅ Positive/flat"
        elif change_pct >= NIFTY_RED:
            return 0, f"Nifty: {change_pct:+.2f}% ⚠️ Mild weakness — caution"
        else:
            return -2, f"Nifty: {change_pct:+.2f}% ❌ Significant drop — skip trades"
    except Exception as e:
        return 0, f"Nifty: Error ({e}) — skipping check"


def check_fii(nse: NSE) -> tuple:
    """Check previous day's FII net activity."""
    try:
        data = nse.advanceDecline()
        # FII data might be in a separate endpoint
        # Try to get from market stats
        fii_net = None

        # advanceDecline sometimes includes FII data
        if isinstance(data, dict):
            fii_net = data.get("fiiNetActivity") or data.get("fii_net")

        if fii_net is None:
            return 0, "FII: Data not available — skipping check"

        fii_net = float(fii_net)
        if fii_net >= 0:
            return 1, f"FII: ₹{fii_net:,.0f} Cr ✅ Net buyers"
        elif fii_net >= FII_RED:
            return 0, f"FII: ₹{fii_net:,.0f} Cr ⚠️ Mild selling — caution"
        else:
            return -2, f"FII: ₹{fii_net:,.0f} Cr ❌ Heavy selling — skip trades"
    except Exception as e:
        return 0, f"FII: Error ({e}) — skipping check"


def check_advance_decline(nse: NSE) -> tuple:
    """Check advance/decline ratio — how many stocks are up vs down."""
    try:
        data = nse.advanceDecline()
        if not data:
            return 0, "A/D: Could not fetch — skipping check"

        # Handle list or dict response
        if isinstance(data, list):
            # Find NSE entry
            nse_data = next((d for d in data if d.get("market") == "NSE"), data[0] if data else {})
        else:
            nse_data = data

        advances = float(nse_data.get("advances", 0) or nse_data.get("advance", 0))
        declines = float(nse_data.get("declines", 0) or nse_data.get("decline", 0))

        if declines == 0:
            return 0, "A/D: Could not calculate ratio"

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
    """
    Run all market checks and return overall trading decision.
    """

    def __init__(self):
        self.nse = get_nse()
        self.score = 0
        self.checks = []
        self.decision = "TRADE"
        self.size_multiplier = 1.0

    def run_all_checks(self) -> dict:
        print("\n📊 Running market sentiment checks...")
        print("─" * 50)

        # Run each check
        checks = [
            ("VIX",      check_vix(self.nse)),
            ("Nifty",    check_nifty(self.nse)),
            ("FII",      check_fii(self.nse)),
            ("A/D Ratio",check_advance_decline(self.nse)),
        ]

        total_score = 0
        for name, (score, message) in checks:
            total_score += score
            self.checks.append({"name": name, "score": score, "message": message})
            print(f"  {message}")

        self.score = total_score

        # Decision
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

        print(f"─" * 50)
        print(f"  Total Score: {total_score}")
        print(f"  Decision:    {verdict}")
        print(f"─" * 50)

        return {
            "score":            total_score,
            "decision":         self.decision,
            "size_multiplier":  self.size_multiplier,
            "verdict":          verdict,
            "checks":           self.checks,
        }

    def check_individual_stock(self, symbol: str, entry: float, current_open: float) -> tuple:
        """Check if individual stock is okay to trade."""
        score, message = check_stock_gap(symbol, entry, current_open)
        return score >= 0, message


def run_sentiment_check() -> dict:
    """Main function — run all checks and return result."""
    sentiment = MarketSentiment()
    return sentiment.run_all_checks()


if __name__ == "__main__":
    result = run_sentiment_check()
    print(f"\nFinal decision: {result['verdict']}")
