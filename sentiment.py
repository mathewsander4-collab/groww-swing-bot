"""
Market sentiment filter — checks market conditions before placing any trades.
Uses NSE India API (allIndices) for VIX, Nifty 50, and A/D ratio.

Scoring system:
  Green   = +1
  Caution =  0
  Red     = -2

Total score:
  +3      → TRADE NORMALLY      (size_multiplier = 1.0)
   0 to +2 → TRADE with 50% size (size_multiplier = 0.5)
  -1 or below → SKIP ALL TRADES  (size_multiplier = 0.0)
"""
import requests

# Thresholds
VIX_GREEN    = 18.0
VIX_RED      = 22.0
NIFTY_GREEN  = -0.3   # % change
NIFTY_RED    = -0.8
AD_GREEN     = 1.5
AD_RED       = 0.8
GAP_DOWN_RED = -2.0   # % gap for individual stocks

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


def get_nse_indices() -> list:
    """Fetch all indices from NSE allIndices API."""
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
        r = session.get("https://www.nseindia.com/api/allIndices", headers=NSE_HEADERS, timeout=10)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        print(f"[SENTIMENT] NSE allIndices failed: {e}")
        return []


def find_index(indices: list, name: str) -> dict:
    """Find index by partial name match."""
    name_upper = name.upper()
    for idx in indices:
        idx_name = str(idx.get("index", "") or idx.get("indexSymbol", "")).upper()
        if name_upper in idx_name:
            return idx
    return {}


def check_vix(indices: list) -> tuple:
    """Check India VIX."""
    try:
        idx = find_index(indices, "VIX")
        if not idx:
            return 0, "VIX: Not found — skipping"

        vix = float(idx.get("last", 0) or 0)
        if vix == 0:
            return 0, "VIX: Could not read value — skipping"

        if vix < VIX_GREEN:
            return 1, f"VIX: {vix:.1f} ✅ Calm market"
        elif vix < VIX_RED:
            return 0, f"VIX: {vix:.1f} ⚠️ Elevated — caution"
        else:
            return -2, f"VIX: {vix:.1f} ❌ High fear — skip trades"
    except Exception as e:
        return 0, f"VIX: Error ({e}) — skipping"


def check_nifty(indices: list) -> tuple:
    """Check Nifty 50 % change."""
    try:
        idx = find_index(indices, "NIFTY 50")
        if not idx:
            return 0, "Nifty: Not found — skipping"

        change_pct = float(idx.get("percentChange", 0) or 0)

        if change_pct >= NIFTY_GREEN:
            return 1, f"Nifty: {change_pct:+.2f}% ✅ Positive/flat"
        elif change_pct >= NIFTY_RED:
            return 0, f"Nifty: {change_pct:+.2f}% ⚠️ Mild weakness — caution"
        else:
            return -2, f"Nifty: {change_pct:+.2f}% ❌ Significant drop — skip trades"
    except Exception as e:
        return 0, f"Nifty: Error ({e}) — skipping"


def check_advance_decline(indices: list) -> tuple:
    """Check A/D ratio from Nifty 50 data."""
    try:
        idx = find_index(indices, "NIFTY 50")
        if not idx:
            return 0, "A/D: Not found — skipping"

        advances = float(idx.get("advances", 0) or 0)
        declines = float(idx.get("declines", 0) or 0)

        if advances == 0 or declines == 0:
            return 0, "A/D: Data not available — skipping"

        ratio = advances / declines

        if ratio >= AD_GREEN:
            return 1, f"A/D: {ratio:.2f} ✅ ({int(advances)} up / {int(declines)} down)"
        elif ratio >= AD_RED:
            return 0, f"A/D: {ratio:.2f} ⚠️ Mixed ({int(advances)} up / {int(declines)} down)"
        else:
            return -2, f"A/D: {ratio:.2f} ❌ Mostly falling ({int(advances)} up / {int(declines)} down)"
    except Exception as e:
        return 0, f"A/D: Error ({e}) — skipping"


def check_stock_gap(symbol: str, entry_price: float, current_open: float) -> tuple:
    """Check if individual stock has gapped down at open."""
    if current_open <= 0 or entry_price <= 0:
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

        indices = get_nse_indices()
        if not indices:
            print("  ⚠️  NSE API unavailable — defaulting to REDUCE size")
            return {
                "score": 0, "decision": "REDUCE", "size_multiplier": 0.5,
                "verdict": "⚠️ TRADE WITH 50% POSITION SIZE (NSE unavailable)",
                "checks": [],
            }

        checks = [
            ("VIX",   check_vix(indices)),
            ("Nifty", check_nifty(indices)),
            ("A/D",   check_advance_decline(indices)),
        ]

        total_score = 0
        for name, (score, message) in checks:
            total_score += score
            self.checks.append({"name": name, "score": score, "message": message})
            print(f"  {message}")

        self.score = total_score

        # Max score = 3 (VIX + Nifty + A/D)
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
        print(f"  Total Score: {total_score}/3")
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
        approved = score >= 0  # blocks gap-down (-2); allows caution (0) and green (1)
        return approved, message


def run_sentiment_check() -> dict:
    sentiment = MarketSentiment()
    return sentiment.run_all_checks()


if __name__ == "__main__":
    result = run_sentiment_check()
    print(f"\nFinal decision: {result['verdict']}")