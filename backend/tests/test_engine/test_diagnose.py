"""Unit tests for engine/diagnose.py"""
import pytest
from datetime import date, timedelta
from app.engine.diagnose import (
    compute_sufficiency, compute_path_safety,
    check_band_mismatch, check_fragmentation, check_no_safe_assets,
)
from tests.conftest import TODAY, make_holding, make_goal, make_assumptions


def make_earmarked(holding, amount):
    return (holding, amount)


def test_sufficiency_on_track():
    a = make_assumptions()
    goal = make_goal("g1", "Education", "education", target_today=500000,
                     horizon_years=10.0, equity_band_low=0.30, equity_band_high=0.60)
    h = make_holding("h1", "Equity", "equity", 1.0, "flexi_cap", 300000)
    result = compute_sufficiency(goal, [(h, 300000)], [], a, TODAY)
    assert result is not None
    assert result.p10 < result.p50 < result.p90
    assert result.target_future_value > 500000
    assert result.illustrative_note != ""
    assert result.judged_against == "p10"  # education is essential


def test_sufficiency_none_for_perpetual():
    a = make_assumptions()
    goal = make_goal("g1", "Wealth", "perpetual_wealth", is_perpetual=True,
                     equity_band_low=0.70, equity_band_high=0.90)
    h = make_holding("h1", "Equity", "equity", 1.0, "flexi_cap", 500000)
    result = compute_sufficiency(goal, [(h, 500000)], [], a, TODAY)
    assert result is None


def test_sufficiency_verdict_not_assured_when_underfunded():
    a = make_assumptions()
    goal = make_goal("g1", "Retirement", "retirement", target_today=10_000_000,
                     horizon_years=10.0, equity_band_low=0.50, equity_band_high=0.75)
    h = make_holding("h1", "Equity", "equity", 1.0, "flexi_cap", 100000)
    result = compute_sufficiency(goal, [(h, 100000)], [], a, TODAY)
    assert result is not None
    assert result.verdict in ("behind", "not_assured")


def test_path_safety_high_fragility_all_equity():
    a = make_assumptions()
    goal = make_goal("g1", "Retirement", "retirement", target_today=500000,
                     horizon_years=10.0, equity_band_low=0.50, equity_band_high=0.75)
    goal.target_future_value = 800000
    h = make_holding("h1", "Equity", "equity", 1.0, "flexi_cap", 100000)
    result = compute_path_safety(goal, [(h, 100000)], a)
    # 100% equity with target far above corpus → should be high or moderate fragility
    assert result.fragility in ("high", "moderate")
    assert len(result.scenarios) == 4


def test_path_safety_low_fragility_well_funded():
    a = make_assumptions()
    goal = make_goal("g1", "Education", "education", target_today=100000,
                     horizon_years=10.0)
    goal.target_future_value = 150000
    h = make_holding("h1", "Liquid", "liquid", 0.0, "liquid", 1_000_000, sigma=0.01)
    result = compute_path_safety(goal, [(h, 1_000_000)], a)
    assert result.fragility == "low"


def test_band_mismatch_too_high():
    goal = make_goal("g1", "Near Term", "near_term_fixed",
                     equity_band_low=0.0, equity_band_high=0.15)
    flag = check_band_mismatch(goal, 0.80)
    assert flag is not None
    assert flag.type == "band_mismatch"


def test_band_mismatch_within_band():
    goal = make_goal("g1", "Education", "education",
                     equity_band_low=0.30, equity_band_high=0.60)
    flag = check_band_mismatch(goal, 0.45)
    assert flag is None


def test_fragmentation_flag():
    a = make_assumptions(**{"fragmentation.max_goals": 5})
    goals = [make_goal(f"g{i}", f"Goal {i}", "education") for i in range(8)]
    flag = check_fragmentation(goals, a)
    assert flag is not None
    assert flag.type == "fragmentation"


def test_no_safe_assets_flag():
    holdings = [make_holding("h1", "Equity", "equity", 1.0, "flexi_cap", 100000)]
    flag = check_no_safe_assets(holdings)
    assert flag is not None
    assert flag.type == "no_safe_assets"


def test_no_safe_assets_flag_not_raised_with_liquid():
    holdings = [
        make_holding("h1", "Equity", "equity", 1.0, "flexi_cap", 100000),
        make_holding("h2", "Liquid", "liquid", 0.0, "liquid", 50000, sigma=0.01),
    ]
    flag = check_no_safe_assets(holdings)
    assert flag is None
