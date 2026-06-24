"""
Walk-forward backtest using Groww historical data.
Caches each symbol's OHLCV to cache/history/ after first download.

Usage:
    python backtest.py --years 3
    python backtest.py --years 3 --refresh-data
"""
import argparse
import os
from datetime import datetime, timedelta

import pandas as pd
from tqdm import tqdm

import config
import indicators
import risk
import strategies
from groww_client import GrowwClient
from universe import build_universe

HISTORY_CACHE_DIR = os.path.join(config.CACHE_DIR, "history")
os.makedirs(HISTORY_CACHE_DIR, exist_ok=True)

ROUND_TRIP_COST_PCT = 0.15  # STT + charges; Groww equity delivery brokerage = 0


def _cache_path(symbol: str) -> str:
    return os.path.join(HISTORY_CACHE_DIR, f"{symbol}.csv")


def load_symbol_history(client: GrowwClient, symbol: str, years: int, refresh: bool) -> pd.DataFrame:
    path = _cache_path(symbol)
    if not refresh and os.path.exists(path):
        df = pd.read_csv(path, parse_dates=["date"])
        if (datetime.now() - df["date"].max()).days <= 3:
            return df

    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=365 * years + 60)).strftime("%Y-%m-%d")
    df = client.get_historical_candles(symbol, from_date, to_date, interval_minutes=1440)
    if not df.empty:
        df.to_csv(path, index=False)
    return df


def load_all_histories(universe: list, years: int, refresh: bool) -> dict:
    client = GrowwClient()
    histories = {}
    for stock in tqdm(universe, desc="Loading price history"):
        symbol = stock["symbol"]
        try:
            df = load_symbol_history(client, symbol, years, refresh)
            if len(df) > config.EMA_TREND + 10:
                histories[symbol] = indicators.add_all_indicators(df, config)
        except Exception as e:
            tqdm.write(f"Skipping {symbol}: {e}")
    return histories


def run_backtest(histories: dict, cfg=config) -> dict:
    all_dates = sorted(set(d for df in histories.values() for d in df["date"]))
    by_symbol = {sym: df.set_index("date") for sym, df in histories.items()}

    cash = cfg.CAPITAL
    open_positions = {}
    pending_entries = []
    closed_trades = []
    equity_curve = []

    for date in tqdm(all_dates, desc="Simulating"):
        # Fill pending entries at today's open
        still_pending = []
        for sig in pending_entries:
            sym = sig["symbol"]
            if sym in open_positions or sym not in by_symbol:
                continue
            if date not in by_symbol[sym].index:
                still_pending.append(sig)
                continue
            row = by_symbol[sym].loc[date]
            entry_price = float(row["open"])
            if not entry_price or not sig["stop"] or entry_price <= sig["stop"]:
                continue  # gapped through stop — skip
            sizing = risk.position_size(cash, entry_price, sig["stop"], cfg)
            if sizing["shares"] <= 0 or len(open_positions) >= cfg.MAX_OPEN_POSITIONS:
                continue
            cost = sizing["capital_used"] * (ROUND_TRIP_COST_PCT / 100 / 2)
            cash -= sizing["capital_used"] + cost
            open_positions[sym] = {
                "entry": entry_price,
                "stop": sig["stop"],
                "target": sig["target"],
                "shares": sizing["shares"],
                "capital_used": sizing["capital_used"],
                "entry_date": date,
                "strategy": sig["strategy"],
            }
        pending_entries = still_pending

        # Manage open positions
        for sym in list(open_positions.keys()):
            if sym not in by_symbol or date not in by_symbol[sym].index:
                continue
            pos = open_positions[sym]
            row = by_symbol[sym].loc[date]
            exit_price, exit_reason = None, None

            if row["low"] <= pos["stop"]:
                exit_price, exit_reason = pos["stop"], "stop"
            elif row["high"] >= pos["target"]:
                exit_price, exit_reason = pos["target"], "target"

            if exit_price is not None:
                proceeds = exit_price * pos["shares"]
                cost = proceeds * (ROUND_TRIP_COST_PCT / 100 / 2)
                cash += proceeds - cost
                pnl = (exit_price - pos["entry"]) * pos["shares"] - cost
                closed_trades.append({
                    "symbol": sym,
                    "strategy": pos["strategy"],
                    "entry_date": pos["entry_date"],
                    "exit_date": date,
                    "entry": pos["entry"],
                    "exit": exit_price,
                    "shares": pos["shares"],
                    "pnl": pnl,
                    "exit_reason": exit_reason,
                })
                del open_positions[sym]

        # Scan for new signals
        if len(open_positions) < cfg.MAX_OPEN_POSITIONS:
            for sym, df in by_symbol.items():
                if sym in open_positions or date not in df.index:
                    continue
                i = df.index.get_loc(date)
                if isinstance(i, slice):
                    continue
                df_reset = df.reset_index()
                sigs = strategies.generate_signals(sym, df_reset, i=i, cfg=cfg)
                for s in sigs:
                    pending_entries.append(s)

        # Mark to market
        mtm = cash
        for sym, pos in open_positions.items():
            if sym in by_symbol and date in by_symbol[sym].index:
                mtm += by_symbol[sym].loc[date]["close"] * pos["shares"]
            else:
                mtm += pos["capital_used"]
        equity_curve.append({"date": date, "equity": mtm})

    return {
        "trades": pd.DataFrame(closed_trades),
        "equity_curve": pd.DataFrame(equity_curve),
        "final_cash": cash,
        "open_at_end": open_positions,
    }


def compute_metrics(result: dict, cfg=config) -> dict:
    trades = result["trades"]
    equity = result["equity_curve"]
    if trades.empty or equity.empty:
        return {"trades": 0, "message": "No trades generated."}

    wins = trades[trades["pnl"] > 0]
    losses = trades[trades["pnl"] <= 0]
    gross_profit = wins["pnl"].sum()
    gross_loss = -losses["pnl"].sum()
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    equity["peak"] = equity["equity"].cummax()
    equity["drawdown"] = (equity["equity"] - equity["peak"]) / equity["peak"]
    max_dd = equity["drawdown"].min() * 100

    start_eq, end_eq = cfg.CAPITAL, equity["equity"].iloc[-1]
    years = max((equity["date"].iloc[-1] - equity["date"].iloc[0]).days / 365.25, 0.1)
    cagr = ((end_eq / start_eq) ** (1 / years) - 1) * 100

    return {
        "total_trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1),
        "profit_factor": round(profit_factor, 2),
        "avg_win_inr": round(wins["pnl"].mean(), 0) if not wins.empty else 0,
        "avg_loss_inr": round(losses["pnl"].mean(), 0) if not losses.empty else 0,
        "max_drawdown_pct": round(max_dd, 1),
        "cagr_pct": round(cagr, 1),
        "final_equity_inr": round(end_eq, 0),
        "starting_capital_inr": cfg.CAPITAL,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--refresh-data", action="store_true")
    parser.add_argument("--refresh-universe", action="store_true")
    args = parser.parse_args()

    uni = build_universe(force_refresh=args.refresh_universe)
    print(f"Loading {args.years}y of history for {len(uni)} symbols...")
    histories = load_all_histories(uni, args.years, args.refresh_data)
    print(f"{len(histories)} symbols had sufficient history.")

    result = run_backtest(histories, config)
    metrics = compute_metrics(result, config)

    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    for k, v in metrics.items():
        print(f"{k:25s}: {v}")

    trades_path = os.path.join(config.DATA_DIR, "backtest_trades.csv")
    result["trades"].to_csv(trades_path, index=False)
    print(f"\nFull trade log → {trades_path}")
