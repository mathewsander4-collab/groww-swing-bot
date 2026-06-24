"""
Swing trading strategies for NSE equities. Each strategy function checks
ONE row (by integer position `i`) of an indicator-enriched DataFrame
(see indicators.add_all_indicators) and returns a signal dict or None.

Using an explicit row index (rather than always df.iloc[-1]) means the
exact same function can be used by:
  - scanner.py: i = len(df) - 1   (today's bar)
  - backtest.py: i looped across the whole history

Signal dict shape:
{
    "symbol": str, "strategy": str, "entry": float, "stop": float,
    "target": float, "score": float, "reason": str
}
"""
from typing import Optional

import pandas as pd

import config


def _r_multiple_target(entry: float, stop: float, reward_risk: float) -> float:
    risk = entry - stop
    return entry + reward_risk * risk


def ema_pullback_signal(symbol: str, df: pd.DataFrame, i: int = -1, cfg=config) -> Optional[dict]:
    """
    Uptrend (fast EMA > slow EMA > trend EMA) with price pulling back toward
    the fast EMA, RSI cooled off but not oversold/broken, and the bar at
    index i closing back above the fast EMA (a bullish reversal day).
    """
    if i < 0:
        i = len(df) + i
    if i < cfg.EMA_TREND + 5:
        return None
    last, prev = df.iloc[i], df.iloc[i - 1]

    uptrend = last["ema_fast"] > last["ema_slow"] > last["ema_trend"]
    if not uptrend:
        return None

    pulled_back = prev["low"] <= prev["ema_fast"] * 1.01  # touched/near the 20EMA
    reclaimed = last["close"] > last["ema_fast"] and last["close"] > last["open"]
    healthy_rsi = cfg.RSI_OVERSOLD < last["rsi"] < cfg.RSI_OVERBOUGHT

    if pulled_back and reclaimed and healthy_rsi and last["adx"] > cfg.ADX_MIN_THRESHOLD:
        entry = float(last["close"])
        stop = float(entry - cfg.STOP_LOSS_ATR_MULT * last["atr"])
        if stop >= entry:
            return None
        target = _r_multiple_target(entry, stop, cfg.REWARD_RISK_RATIO)
        score = float(last["adx"])  # trend strength as a ranking score
        return {
            "symbol": symbol,
            "strategy": "ema_pullback",
            "date": last["date"],
            "entry": entry,
            "stop": stop,
            "target": target,
            "score": score,
            "reason": f"Uptrend pullback to 20EMA, reclaimed with RSI {last['rsi']:.0f}, ADX {last['adx']:.0f}",
        }
    return None


def breakout_signal(symbol: str, df: pd.DataFrame, i: int = -1, cfg=config) -> Optional[dict]:
    """
    Price closes above the N-day rolling high (computed BEFORE today) on
    volume well above its 20-day average, confirming a breakout with
    conviction.
    """
    if i < 0:
        i = len(df) + i
    if i < cfg.BREAKOUT_LOOKBACK + 5:
        return None
    last = df.iloc[i]
    prior_high = df.iloc[i - 1]["rolling_high"]  # high BEFORE today's candle

    breakout = last["close"] > prior_high
    volume_confirmed = last["volume"] > cfg.VOLUME_SURGE_MULT * last["vol_sma"]
    trending = last["adx"] > 20

    if breakout and volume_confirmed and last["adx"] > cfg.ADX_MIN_THRESHOLD:
        entry = float(last["close"])
        stop = float(entry - cfg.STOP_LOSS_ATR_MULT * last["atr"])
        if stop >= entry:
            return None
        target = _r_multiple_target(entry, stop, cfg.REWARD_RISK_RATIO)
        score = float(last["volume"] / last["vol_sma"]) * 10  # volume surge magnitude
        return {
            "symbol": symbol,
            "strategy": "breakout",
            "date": last["date"],
            "entry": entry,
            "stop": stop,
            "target": target,
            "score": score,
            "reason": f"{cfg.BREAKOUT_LOOKBACK}-day breakout on {last['volume']/last['vol_sma']:.1f}x volume, ADX {last['adx']:.0f}",
        }
    return None


def momentum_score(df: pd.DataFrame, i: int = -1, cfg=config) -> Optional[float]:
    """Trailing N-day return ending at bar i, used to rank relative strength."""
    if i < 0:
        i = len(df) + i
    if i < cfg.MOMENTUM_LOOKBACK_DAYS + 1:
        return None
    past = df.iloc[i - cfg.MOMENTUM_LOOKBACK_DAYS]["close"]
    now = df.iloc[i]["close"]
    if past <= 0:
        return None
    return float((now / past - 1) * 100)


STRATEGY_FUNCS = (ema_pullback_signal, breakout_signal)


def generate_signals(symbol: str, df: pd.DataFrame, i: int = -1, cfg=config) -> list:
    """Runs every strategy against bar i of one symbol's indicator-enriched DataFrame."""
    signals = []
    for fn in STRATEGY_FUNCS:
        sig = fn(symbol, df, i, cfg)
        if sig:
            signals.append(sig)
    return signals
