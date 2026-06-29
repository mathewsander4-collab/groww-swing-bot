"""
Price fetcher — uses Groww get_historical_candles for individual stock prices.
NSE quote-equity API is blocked (403). Groww historical works fine.
"""
from datetime import datetime, timedelta


def fetch_nse_price(symbol: str) -> dict:
    """Fetch latest price for a stock using Groww historical candles."""
    try:
        from groww_client import GrowwClient
        client    = GrowwClient()
        to_date   = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        df = client.get_historical_candles(symbol, from_date, to_date, interval_minutes=1440)

        if df is None or df.empty:
            return {"error": "No data from Groww"}

        df.columns = [c.lower() for c in df.columns]
        latest = df.iloc[-1]
        prev   = df.iloc[-2] if len(df) > 1 else latest

        close      = float(latest.get("close", 0) or 0)
        open_      = float(latest.get("open",  0) or 0)
        high       = float(latest.get("high",  0) or 0)
        low        = float(latest.get("low",   0) or 0)
        volume     = int(latest.get("volume",  0) or 0)
        prev_close = float(prev.get("close",   0) or 0)

        if close == 0:
            return {"error": "Close price is 0"}

        change     = close - prev_close
        change_pct = round(change / prev_close * 100, 2) if prev_close else 0

        return {
            "open":       round(open_, 2),
            "high":       round(high, 2),
            "low":        round(low, 2),
            "close":      round(close, 2),
            "prev_close": round(prev_close, 2),
            "change":     round(change, 2),
            "change_pct": change_pct,
            "volume":     volume,
            "source":     "Groww API"
        }
    except Exception as e:
        return {"error": str(e)}


def get_ltp(symbol: str) -> float:
    """Quick last traded price. Returns 0 on failure."""
    result = fetch_nse_price(symbol)
    return result.get("close", 0)