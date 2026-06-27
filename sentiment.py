"""
Market sentiment filter — checks market conditions before placing any trades.
Uses yfinance for ^NSEI (Nifty 50) and ^INDIAVIX data.
Groww API has no index endpoint — yfinance is the reliable fallback.

Scoring system:
  Green   = +1
  Caution =  0
  Red     = -2

Total score:
  +2 to +3  → TRADE NORMALLY      (size_multiplier = 1.0)
   0 to +1  → TRADE with 50% size (size_multiplier = 0.5)
  -1 or below → SKIP ALL TRADES   (size_multiplier = 0.0)
"""
import yfinance as yf

# Thresholds
VIX_GREEN    = 18.0
VIX_RED      = 22.0
NIFTY_GREEN  = -0.3   # % change
NIFTY_RED    = -0.8
GAP_DOWN_RED = -2.0   # % gap for individual stocks


def _pct_change(last: float, prev_close: float) -> float:
    """Calculate % change from previous close."""
    if prev_close and prev_close != 0:
        return (last - prev_close) / prev_close * 100
    return 0.0


def check_vix() -> tuple:
    """Check India VIX via yfinance ^INDIAVIX."""
    try:
        info = yf.Ticker("^INDIAVIX").fast_info
        vix = float(info.get("lastPrice") or info.get("previousClose") or 0)
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


def check_nifty() -> tuple:
    """Check Nifty 50 trend via yfinance ^NSEI."""
    try:
        info = yf.Ticker("^NSEI").fast_info
        last       = float(info.get("lastPrice") or 0)
        prev_close = float(info.get("previousClose") or info.get("regularMarketPreviousClose") or 0)

        if last == 0:
            return 0, "Nifty: Could not read value — skipping"

        change_pct = _pct_change(last, prev_close)

        if change_pct >= NIFTY_GREEN:
            return 1, f"Nifty: {change_pct:+.2f}% ✅ Positive/flat"
        elif change_pct >= NIFTY_RED:
            return 0, f"Nifty: {change_pct:+.2f}% ⚠️ Mild weakness — caution"
        else:
            return -2, f"Nifty: {change_pct:+.2f}% ❌ Significant drop — skip trades"
    except Exception as e:
        return 0, f"Nifty: Error ({e}) — skipping"


def check_stock_gap(symbol: str, entry_price: float, current_open: float) -> tuple:
    """Check if individual stock has gapped down significantly at open."""
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

        checks = [
            ("VIX",   check_vix()),
            ("Nifty", check_nifty()),
        ]

        total_score = 0
        for name, (score, message) in checks:
            total_score += score
            self.checks.append({"name": name, "score": score, "message": message})
            print(f"  {message}")

        # FII and A/D not available — noted explicitly
        print("  FII: Not available via yfinance — skipping")
        print("  A/D: Not available via yfinance — skipping")

        self.score = total_score

        # Max possible score is now 2 (VIX + Nifty), so thresholds adjusted
        if total_score >= 2:
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
        print(f"  Total Score: {total_score}/2")
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
        """Returns (approved: bool, message: str). Gap-down stocks are blocked."""
        score, message = check_stock_gap(symbol, entry, current_open)
        approved = score >= 0  # blocks only gap-down (-2); allows caution (0) and green (1)
        return approved, message


def run_sentiment_check() -> dict:
    sentiment = MarketSentiment()
    return sentiment.run_all_checks()


if __name__ == "__main__":
    result = run_sentiment_check()
    print(f"\nFinal decision: {result['verdict']}")