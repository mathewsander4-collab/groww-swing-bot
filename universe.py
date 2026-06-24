"""
Builds and caches the NIFTY 500 tradeable universe.
Groww uses plain NSE trading symbols directly (e.g. RELIANCE, TATAMOTORS)
so no instrument_key mapping is needed — much simpler than Upstox.
"""
import io
import json
import os
import time

import pandas as pd
import requests

import config

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}


def _cache_is_fresh() -> bool:
    if not os.path.exists(config.UNIVERSE_CACHE_FILE):
        return False
    age = time.time() - os.path.getmtime(config.UNIVERSE_CACHE_FILE)
    return age < config.UNIVERSE_CACHE_DAYS * 86400


def build_universe(force_refresh: bool = False) -> list:
    """
    Returns a list of dicts: {symbol, name, industry}
    Cached locally for config.UNIVERSE_CACHE_DAYS days.
    """
    if not force_refresh and _cache_is_fresh():
        with open(config.UNIVERSE_CACHE_FILE) as f:
            return json.load(f)

    print("Downloading NIFTY 500 constituent list from NSE...")
    resp = requests.get(config.NIFTY500_CSV_URL, headers=NSE_HEADERS, timeout=config.REQUEST_TIMEOUT)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns={"Company Name": "name", "Symbol": "symbol", "Industry": "industry"})
    universe = df[["symbol", "name", "industry"]].to_dict("records")

    with open(config.UNIVERSE_CACHE_FILE, "w") as f:
        json.dump(universe, f, indent=2)
    print(f"Universe built: {len(universe)} symbols cached.")
    return universe


if __name__ == "__main__":
    u = build_universe(force_refresh=True)
    print(f"Total: {len(u)} symbols. Sample: {u[:3]}")
