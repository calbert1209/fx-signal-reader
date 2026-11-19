"""Currency pair profiles and the built-in pair registry.

A :class:`PairProfile` captures every pair-specific convention (pip scale,
display precision, data symbols, default bracket, calendar currencies, risk
policy) so the rest of the pipeline does not need USD/JPY assumptions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Union


class RiskPolicyType(str, Enum):
    VERBAL_LADDER = "VERBAL_LADDER"  # MoF / BoJ verbal escalation & barriers
    CENTRAL_BANK = "CENTRAL_BANK"  # ECB / Fed macro catalyst & blackout


@dataclass(frozen=True)
class PairProfile:
    pair: str  # e.g. "EUR/USD" or "USD/JPY"
    symbol_slug: str  # e.g. "eurusd" or "usdjpy"
    base_currency: str
    quote_currency: str
    spot_symbol: str  # Yahoo ticker, e.g. "EURUSD=X", "JPY=X"
    pip_size: float  # 0.0001 for EURUSD, 0.01 for USDJPY
    price_precision: int  # 5 for EURUSD, 3 for USDJPY
    default_sl_pips: float
    default_tp_pips: float
    base_yield_symbol: str
    quote_yield_symbol: str
    equity_benchmark: str
    calendar_currencies: Set[str] = field(default_factory=set)
    risk_policy: RiskPolicyType = RiskPolicyType.CENTRAL_BANK
    psychological_barriers: List[float] = field(default_factory=list)
    spread_bullish_threshold: float = 2.0
    spread_bearish_threshold: float = 1.0

    @property
    def is_quote_usd(self) -> bool:
        return self.quote_currency == "USD"

    @property
    def is_base_usd(self) -> bool:
        return self.base_currency == "USD"


PAIR_PROFILES: Dict[str, PairProfile] = {
    "USD/JPY": PairProfile(
        pair="USD/JPY",
        symbol_slug="usdjpy",
        base_currency="USD",
        quote_currency="JPY",
        spot_symbol="JPY=X",
        pip_size=0.01,
        price_precision=3,
        default_sl_pips=75.0,
        default_tp_pips=150.0,
        base_yield_symbol="^TNX",
        quote_yield_symbol="JGB10Y",
        equity_benchmark="^N225",
        calendar_currencies={"USD", "JPY"},
        risk_policy=RiskPolicyType.VERBAL_LADDER,
        psychological_barriers=[157.0, 158.5, 160.0],
        spread_bullish_threshold=2.0,
        spread_bearish_threshold=1.0,
    ),
    "EUR/USD": PairProfile(
        pair="EUR/USD",
        symbol_slug="eurusd",
        base_currency="EUR",
        quote_currency="USD",
        spot_symbol="EURUSD=X",
        pip_size=0.0001,
        price_precision=5,
        default_sl_pips=50.0,
        default_tp_pips=100.0,
        base_yield_symbol="DE10Y",
        quote_yield_symbol="^TNX",
        equity_benchmark="^STOXX50E",
        calendar_currencies={"EUR", "USD"},
        risk_policy=RiskPolicyType.CENTRAL_BANK,
        psychological_barriers=[1.0000, 1.0500, 1.1000],
        spread_bullish_threshold=-1.0,
        spread_bearish_threshold=-2.0,
    ),
}

DEFAULT_PAIR = "USD/JPY"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _normalize_pair_name(name: str) -> str:
    return re.sub(r"[^A-Z]", "", name.upper())


def get_pair_profile(pair_name: Union[str, PairProfile]) -> PairProfile:
    """Resolve a profile from "USD/JPY", "usdjpy", "EUR-USD", "EURUSD", etc."""
    if isinstance(pair_name, PairProfile):
        return pair_name
    if not isinstance(pair_name, str):
        raise ValueError(f"Unknown currency pair: {pair_name!r}")
    key = _normalize_pair_name(pair_name)
    for profile in PAIR_PROFILES.values():
        if key == _normalize_pair_name(profile.pair):
            return profile
    supported = ", ".join(PAIR_PROFILES)
    raise ValueError(f"Unknown currency pair: {pair_name!r}. Supported pairs: {supported}")


def get_pair_db_path(
    pair: Union[str, PairProfile], base_dir: Optional[Union[str, Path]] = None
) -> Path:
    """Return ``<base_dir>/data/{symbol_slug}_signals.db`` (base_dir defaults to project root)."""
    profile = get_pair_profile(pair)
    root = Path(base_dir) if base_dir is not None else _PROJECT_ROOT
    return root / "data" / f"{profile.symbol_slug}_signals.db"
