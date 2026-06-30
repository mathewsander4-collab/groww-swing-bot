"""
Daily scanner — run after market close (after 3:30 PM IST).
Screens the full NIFTY 500 universe for swing setups using Groww API.

Usage:
    python scanner.py
    python scanner.py --refresh-universe
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

import pandas as pd
from tqdm import tqdm

import config

# Disable tqdm's fancy terminal control on non-TTY environments (Railway) —
# colorama can recurse infinitely trying to render progress bars there.
TQDM_DISABLE = not sys.stdout.isatty()
import indicators
import risk
import strategies
from groww_client import GrowwClient
from universe import build_universe


def fetch_history(client: GrowwClient, symbol: str) -> pd.DataFrame:
    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=config.HISTORY_DAYS)).strftime("%Y-%m-%d")
    return client.get_historical_candles(symbol, from_date, to_date, interval_minutes=1440)


def passes_liquidity_filter(df: pd.DataFrame) -> bool:
    if len(df) < 20:
        return False
    last_close = df.iloc[-1]["close"]
    avg_vol_20 = df["volume"].tail(20).mean()
    return last_close >= config.MIN_PRICE and avg_vol_20 >= config.MIN_AVG_DAILY_VOLUME


def run_scan(refresh_universe: bool = False) -> pd.DataFrame:
    universe = build_universe(force_refresh=refresh_universe)
    client = GrowwClient()

    all_signals = []
    failures = []

    for stock in tqdm(universe, desc="Scanning NIFTY 500", disable=TQDM_DISABLE, mininterval=10.0):
        symbol = stock["symbol"]
        try:
            df = fetch_history(client, symbol)
            if not passes_liquidity_filter(df):
                continue
            enriched = indicators.add_all_indicators(df, config)
            sigs = strategies.generate_signals(symbol, enriched, i=-1, cfg=config)
            for s in sigs:
                s["industry"] = stock.get("industry", "")
                all_signals.append(s)
        except Exception as e:
            failures.append((symbol, str(e)))

    if failures:
        print(f"\n{len(failures)} symbols failed (first 5): {failures[:5]}")

    if not all_signals:
        print("No setups found today.")
        return pd.DataFrame()

    signals_df = pd.DataFrame(all_signals).sort_values("score", ascending=False)

    sized_rows = []
    open_count = 0
    for _, row in signals_df.iterrows():
        if open_count >= config.MAX_OPEN_POSITIONS:
            break
        sizing = risk.position_size(config.CAPITAL, row["entry"], row["stop"], config)
        if sizing["shares"] <= 0:
            continue
        r = row.to_dict()
        r.update(sizing)
        r["risk_pct"] = (sizing["risk_amount"] / config.CAPITAL) * 100
        sized_rows.append(r)
        open_count += 1

    results_df = pd.DataFrame(sized_rows)

    # Save to Google Sheets Signals tab (primary store — survives Railway redeploys)
    try:
        from sheets import get_client, sync_signals
        import config
        client   = get_client()
        workbook = client.open_by_key(config.GOOGLE_SHEET_ID)
        sync_signals(workbook, df=results_df)
        print("✅ Signals saved to Google Sheets")
    except Exception as e:
        print(f"Sheets signals save failed: {e}")

    return results_df


def print_report(df: pd.DataFrame):
    if df.empty:
        return
    cols = ["symbol", "strategy", "entry", "stop", "target", "shares", "capital_used", "risk_pct", "reason"]
    display_df = df[cols].copy()
    for c in ["entry", "stop", "target", "capital_used"]:
        display_df[c] = display_df[c].round(2)
    display_df["risk_pct"] = display_df["risk_pct"].round(2)
    print("\n" + "=" * 100)
    print(f"SWING SETUPS — {datetime.now().strftime('%Y-%m-%d')}  (capital: Rs.{config.CAPITAL:,.0f})")
    print("=" * 100)
    try:
        from tabulate import tabulate
        print(tabulate(display_df, headers="keys", tablefmt="psql", showindex=False))
    except ImportError:
        print(display_df.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-universe", action="store_true")
    args = parser.parse_args()

    results = run_scan(refresh_universe=args.refresh_universe)
    print_report(results)

    if not results.empty:
        out_path = f"{config.DATA_DIR}/scan_{datetime.now().strftime('%Y%m%d')}.csv"
        results.to_csv(out_path, index=False)
        print(f"\nSaved to {out_path}")