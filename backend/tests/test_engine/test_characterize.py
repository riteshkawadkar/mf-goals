"""Unit tests for engine/characterize.py"""
import pytest
from datetime import date, timedelta
from app.engine.characterize import (
    characterize_goal, classify_holding, compute_equity_band,
    years_to_date, get_mu_sigma,
)
from tests.conftest import TODAY, make_assumptions


def test_equity_band_lt3y():
    a = make_assumptions()
    low, high = compute_equity_band(2.0, a)
    assert low == 0.0
    assert high == 0.15


def test_equity_band_long_horizon():
    a = make_assumptions()
    low, high = compute_equity_band(20.0, a)
    assert low == 0.70
    assert high == 0.90


def test_characterize_emergency_goal():
    a = make_assumptions()
    result = characterize_goal(
        name="Emergency",
        archetype="emergency",
        target_today=300000,
        horizon_date=None,
        priority=1,
        inflation_rate=None,
        assumptions=a,
        today=TODAY,
    )
    assert result["equity_band_low"] == 0.0
    assert result["equity_band_high"] == 0.0
    assert result["confidence_tag"] == "high"
    assert result["is_perpetual"] is False


def test_characterize_retirement_goal():
    a = make_assumptions()
    horizon = TODAY + timedelta(days=365 * 20)
    result = characterize_goal(
        name="Retirement",
        archetype="retirement",
        target_today=10_000_000,
        horizon_date=horizon,
        priority=1,
        inflation_rate=None,
        assumptions=a,
        today=TODAY,
    )
    assert result["confidence_tag"] == "low"
    assert result["equity_band_low"] >= 0.50
    assert result["equity_band_high"] <= 1.0
    assert result["target_future_value"] > 10_000_000  # inflated


def test_characterize_perpetual_wealth():
    a = make_assumptions()
    result = characterize_goal(
        name="Wealth",
        archetype="perpetual_wealth",
        target_today=None,
        horizon_date=None,
        priority=5,
        inflation_rate=None,
        assumptions=a,
        today=TODAY,
    )
    assert result["is_perpetual"] is True
    assert result["target_future_value"] is None


def test_classify_holding_liquid():
    ac, eq, cluster, tags = classify_holding("Liquid Fund")
    assert ac == "liquid"
    assert eq == 0.0


def test_classify_holding_small_cap():
    ac, eq, cluster, tags = classify_holding("Small Cap Fund")
    assert ac == "equity"
    assert eq == 1.0
    assert cluster == "small_cap"


def test_classify_holding_elss():
    ac, eq, cluster, tags = classify_holding("ELSS Tax Saving Fund")
    assert ac == "equity"
    assert cluster == "elss"


def test_get_mu_sigma_small_cap():
    a = make_assumptions()
    mu, sigma = get_mu_sigma("small_cap", a)
    assert mu == 0.13
    assert sigma == 0.24


def test_inflation_defaults_by_archetype():
    a = make_assumptions()
    result = characterize_goal(
        name="Education",
        archetype="education",
        target_today=500000,
        horizon_date=TODAY + timedelta(days=365 * 10),
        priority=1,
        inflation_rate=None,
        assumptions=a,
        today=TODAY,
    )
    assert result["inflation_rate"] == 0.10  # education inflation default
