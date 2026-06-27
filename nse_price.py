"""
NSE price fetcher — shared utility used by eod_report, exit_manager, trader.
Single NSE session reused across calls for efficiency.
"""
import requests

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

_session = None


def _get_session():
    """Get or create a persistent NSE session."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
    return _session


def fetch_nse_price(symbol: str) -> dict:
    """Fetch price data for a stock from NSE quote-equity API."""
    try:
        session = _get_session()
        r = session.get(
            f"https://www.nseindia.com/api/quote-equity?symbol={symbol}",
            headers=NSE_HEADERS, timeout=10
        )
        data = r.json()
        price_info = data.get("priceInfo", {})

        close  = float(price_info.get("lastPrice", 0) or price_info.get("close", 0) or 0)
        open_  = float(price_info.get("open", 0) or 0)
        high   = float(price_info.get("intraDayHighLow", {}).get("max", 0) or 0)
        low    = float(price_info.get("intraDayHighLow", {}).get("min", 0) or 0)
        prev   = float(price_info.get("previousClose", 0) or open_ or 0)
        volume = int(data.get("marketDeptOrderBook", {}).get("totalSellQuantity", 0) or 0)

        if close == 0:
            return {"error": "Close price is 0"}

        change     = close - prev
        change_pct = (change / prev * 100) if prev else 0

        return {
            "open":       round(open_, 2),
            "high":       round(high, 2),
            "low":        round(low, 2),
            "close":      round(close, 2),
            "prev_close": round(prev, 2),
            "change":     round(change, 2),
            "change_pct": round(change_pct, 2),
            "volume":     volume,
            "source":     "NSE API"
        }
    except Exception as e:
        return {"error": str(e)}


def get_ltp(symbol: str) -> float:
    """Quick last traded price fetch. Returns 0 on failure."""
    result = fetch_nse_price(symbol)
    return result.get("close", 0)