from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from fx_signal_reader.core import DEFAULT_DB_PATH, PROJECT_ROOT
from fx_signal_reader.core import pair_profile as pp
from fx_signal_reader.core.pair_profile import (
    PAIR_PROFILES,
    PairProfile,
    RiskPolicyType,
    get_pair_db_path,
    get_pair_profile,
)


def test_usdjpy_profile_matches_baseline():
    p = get_pair_profile("USD/JPY")
    assert p.pair == "USD/JPY"
    assert p.symbol_slug == "usdjpy"
    assert (p.base_currency, p.quote_currency) == ("USD", "JPY")
    assert p.spot_symbol == "JPY=X"
    assert p.pip_size == 0.01
    assert p.price_precision == 3
    assert (p.default_sl_pips, p.default_tp_pips) == (75.0, 150.0)
    assert (p.base_yield_symbol, p.quote_yield_symbol) == ("^TNX", "JGB10Y")
    assert p.equity_benchmark == "^N225"
    assert p.calendar_currencies == {"USD", "JPY"}
    assert p.risk_policy is RiskPolicyType.VERBAL_LADDER
    assert p.psychological_barriers == [157.0, 158.5, 160.0]
    assert p.is_base_usd and not p.is_quote_usd


def test_eurusd_profile_fields():
    p = get_pair_profile("EUR/USD")
    assert p.pair == "EUR/USD"
    assert p.symbol_slug == "eurusd"
    assert (p.base_currency, p.quote_currency) == ("EUR", "USD")
    assert p.spot_symbol == "EURUSD=X"
    assert p.pip_size == 0.0001
    assert p.price_precision == 5
    assert (p.default_sl_pips, p.default_tp_pips) == (50.0, 100.0)
    assert (p.base_yield_symbol, p.quote_yield_symbol) == ("DE10Y", "^TNX")
    assert p.equity_benchmark == "^STOXX50E"
    assert p.calendar_currencies == {"EUR", "USD"}
    assert p.risk_policy is RiskPolicyType.CENTRAL_BANK
    assert p.psychological_barriers == [1.0, 1.05, 1.1]
    assert p.is_quote_usd and not p.is_base_usd


def test_registry_keys():
    assert set(PAIR_PROFILES) == {"USD/JPY", "EUR/USD"}
    for key, profile in PAIR_PROFILES.items():
        assert key == profile.pair


@pytest.mark.parametrize("name", ["USD/JPY", "USDJPY", "usd/jpy", "usdjpy", " UsdJpy ", "USD-JPY", "USD_JPY"])
def test_lookup_usdjpy_variants(name):
    assert get_pair_profile(name) is PAIR_PROFILES["USD/JPY"]


@pytest.mark.parametrize("name", ["EUR/USD", "EURUSD", "eur/usd", "eurusd", "Eur-Usd"])
def test_lookup_eurusd_variants(name):
    assert get_pair_profile(name) is PAIR_PROFILES["EUR/USD"]


@pytest.mark.parametrize("name", ["GBP/USD", "", "XYZ", "USD", "JPY=X", None])
def test_lookup_unknown_raises(name):
    with pytest.raises(ValueError):
        get_pair_profile(name)


def test_lookup_accepts_profile_instance():
    p = PAIR_PROFILES["EUR/USD"]
    assert get_pair_profile(p) is p


def test_profile_is_frozen():
    with pytest.raises(FrozenInstanceError):
        PAIR_PROFILES["USD/JPY"].pip_size = 1.0


def test_risk_policy_enum_values():
    assert RiskPolicyType.VERBAL_LADDER.value == "VERBAL_LADDER"
    assert RiskPolicyType.CENTRAL_BANK.value == "CENTRAL_BANK"
    assert RiskPolicyType("CENTRAL_BANK") is RiskPolicyType.CENTRAL_BANK


def test_dataclass_defaults():
    p = PairProfile(
        pair="X/Y", symbol_slug="xy", base_currency="X", quote_currency="Y",
        spot_symbol="XY=X", pip_size=0.0001, price_precision=5,
        default_sl_pips=1.0, default_tp_pips=2.0, base_yield_symbol="a",
        quote_yield_symbol="b", equity_benchmark="c",
    )
    assert p.calendar_currencies == set()
    assert p.psychological_barriers == []
    assert p.risk_policy is RiskPolicyType.CENTRAL_BANK
    assert not p.is_base_usd and not p.is_quote_usd


def test_db_path_usdjpy_matches_existing_default():
    assert get_pair_db_path("USDJPY") == DEFAULT_DB_PATH
    assert get_pair_db_path("USD/JPY") == PROJECT_ROOT / "data" / "usdjpy_signals.db"


def test_db_path_eurusd_and_base_dir(tmp_path):
    assert get_pair_db_path("EUR/USD") == PROJECT_ROOT / "data" / "eurusd_signals.db"
    assert get_pair_db_path("eurusd", tmp_path) == tmp_path / "data" / "eurusd_signals.db"
    assert get_pair_db_path(PAIR_PROFILES["USD/JPY"], str(tmp_path)) == Path(tmp_path) / "data" / "usdjpy_signals.db"


def test_db_path_unknown_raises():
    with pytest.raises(ValueError):
        get_pair_db_path("GBPUSD")


def test_exports_from_core():
    import fx_signal_reader.core as core

    for name in ("PairProfile", "RiskPolicyType", "PAIR_PROFILES", "get_pair_profile", "get_pair_db_path"):
        assert name in core.__all__
        assert getattr(core, name) is getattr(pp, name)
