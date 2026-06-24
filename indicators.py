"""
Standard technical indicators, computed with pandas. No TA-Lib dependency
so this runs anywhere.
"""
import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Simplified ADX approximation (not the exact Wilder smoothing used by most
    charting platforms). Good enough as a relative trend-strength filter for
    the strategies here, but don't expect it to match TradingView/broker
    ADX values to the decimal.
    """
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = atr(df, period) * period  # un-smoothed true range scale, approximate Wilder TR sum
    atr_smooth = atr(df, period)

    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_smooth.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_smooth.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx_val.fillna(0)


def bollinger_bands(series: pd.Series, period: int = 20, num_std: float = 2.0):
    mid = sma(series, period)
    std = series.rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def volume_sma(volume: pd.Series, period: int = 20) -> pd.Series:
    return volume.rolling(period).mean()


def add_all_indicators(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """Adds every indicator the strategies need as new columns, in place style (returns copy)."""
    out = df.copy()
    out["ema_fast"] = ema(out["close"], cfg.EMA_FAST)
    out["ema_slow"] = ema(out["close"], cfg.EMA_SLOW)
    out["ema_trend"] = ema(out["close"], cfg.EMA_TREND)
    out["rsi"] = rsi(out["close"], cfg.RSI_PERIOD)
    out["atr"] = atr(out, cfg.ATR_PERIOD)
    out["adx"] = adx(out, cfg.ADX_PERIOD)
    out["vol_sma"] = volume_sma(out["volume"], cfg.BREAKOUT_LOOKBACK)
    out["rolling_high"] = out["high"].rolling(cfg.BREAKOUT_LOOKBACK).max()
    macd_line, signal_line, hist = macd(out["close"])
    out["macd"] = macd_line
    out["macd_signal"] = signal_line
    out["macd_hist"] = hist
    return out
