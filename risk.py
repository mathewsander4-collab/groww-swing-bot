"""
Position sizing and portfolio-level risk constraints.
"""
from dataclasses import dataclass

import config


@dataclass
class Position:
    symbol: str
    entry: float
    stop: float
    target: float
    shares: int
    capital_used: float
    risk_amount: float
    strategy: str
    entry_date: str = ""


def position_size(capital: float, entry: float, stop: float, cfg=config) -> dict:
    import math
    if not entry or not stop or math.isnan(entry) or math.isnan(stop):
        return {"shares": 0, "capital_used": 0.0, "risk_amount": 0.0}
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return {"shares": 0, "capital_used": 0.0, "risk_amount": 0.0}

    risk_amount = capital * (cfg.RISK_PER_TRADE_PCT / 100)
    shares_by_risk = int(risk_amount / risk_per_share)

    max_capital_for_trade = capital * (cfg.MAX_CAPITAL_PER_TRADE_PCT / 100)
    shares_by_capital = int(max_capital_for_trade / entry)

    shares = max(0, min(shares_by_risk, shares_by_capital))
    capital_used = shares * entry
    actual_risk = shares * risk_per_share

    return {"shares": shares, "capital_used": capital_used, "risk_amount": actual_risk}


def build_position(signal: dict, capital: float, cfg=config) -> Position:
    sizing = position_size(capital, signal["entry"], signal["stop"], cfg)
    return Position(
        symbol=signal["symbol"],
        entry=signal["entry"],
        stop=signal["stop"],
        target=signal["target"],
        shares=sizing["shares"],
        capital_used=sizing["capital_used"],
        risk_amount=sizing["risk_amount"],
        strategy=signal["strategy"],
    )


def can_open_new_position(open_positions: list, cfg=config) -> bool:
    return len(open_positions) < cfg.MAX_OPEN_POSITIONS


def available_capital(total_capital: float, open_positions: list) -> float:
    used = sum(p.capital_used for p in open_positions)
    return max(0.0, total_capital - used)
