"""
Groww Cloud API client using the official growwapi Python SDK.
Uses the new get_historical_candles (backtesting) API with pagination
since max duration per request is 180 days for daily candles.
"""
import time
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd
from growwapi import GrowwAPI

import config
from groww_auth import load_token


class GrowwClient:
    def __init__(self, token: Optional[str] = None):
        access_token = token or load_token()
        self.api = GrowwAPI(access_token)

    def get_historical_candles(
        self,
        symbol: str,
        from_date: str,
        to_date: str,
        interval_minutes: int = 1440,
    ) -> pd.DataFrame:
        """
        Fetch daily OHLCV candles using the new backtesting API.
        Max 180 days per request — automatically paginates for longer periods.
        symbol: plain NSE symbol e.g. 'RELIANCE' (we add 'NSE-' prefix internally)
        """
        groww_symbol = f"NSE-{symbol}"
        candle_interval = self.api.CANDLE_INTERVAL_DAY

        start = datetime.strptime(from_date, "%Y-%m-%d")
        end   = datetime.strptime(to_date,   "%Y-%m-%d")

        all_candles = []
        chunk_start = start

        while chunk_start < end:
            chunk_end = min(chunk_start + timedelta(days=179), end)

            for attempt in range(config.REQUEST_RETRY):
                try:
                    resp = self.api.get_historical_candles(
                        exchange=self.api.EXCHANGE_NSE,
                        segment=self.api.SEGMENT_CASH,
                        groww_symbol=groww_symbol,
                        start_time=chunk_start.strftime("%Y-%m-%d 09:15:00"),
                        end_time=chunk_end.strftime("%Y-%m-%d 15:30:00"),
                        candle_interval=candle_interval,
                    )
                    candles = resp.get("candles", []) if isinstance(resp, dict) else []
                    all_candles.extend(candles)
                    break
                except Exception as e:
                    if attempt == config.REQUEST_RETRY - 1:
                        raise RuntimeError(f"Historical data failed for {symbol}: {e}")
                    time.sleep(0.5 * (attempt + 1))

            chunk_start = chunk_end + timedelta(days=1)
            time.sleep(config.REQUEST_SLEEP_BETWEEN_CALLS)

        if not all_candles:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

        # New API candle format: [timestamp_str, open, high, low, close, volume, oi]
        df = pd.DataFrame(all_candles, columns=["date", "open", "high", "low", "close", "volume", "oi"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
        return df

    def get_quotes(self, symbols: List[str]) -> dict:
        result = {}
        for sym in symbols:
            try:
                result[sym] = self.api.get_ohlc(
                    trading_symbol=sym,
                    exchange=self.api.EXCHANGE_NSE,
                    segment=self.api.SEGMENT_CASH,
                )
                time.sleep(0.1)
            except Exception as e:
                result[sym] = {"error": str(e)}
        return result

    def place_order(self, symbol: str, quantity: int,
                    transaction_type: str = "BUY",
                    order_type: str = "MARKET", price: float = 0.0) -> dict:
        return self.api.place_order(
            trading_symbol=symbol,
            exchange=self.api.EXCHANGE_NSE,
            segment=self.api.SEGMENT_CASH,
            transaction_type=transaction_type,
            order_type=order_type,
            quantity=quantity,
            price=price if order_type == "LIMIT" else 0,
            product=self.api.PRODUCT_CNC,
            validity=self.api.VALIDITY_DAY,
        )

    def get_positions(self) -> list:
        resp = self.api.get_positions_for_user(segment=self.api.SEGMENT_CASH)
        return resp if isinstance(resp, list) else []

    def get_holdings(self) -> list:
        resp = self.api.get_holdings_for_user()
        return resp if isinstance(resp, list) else []
