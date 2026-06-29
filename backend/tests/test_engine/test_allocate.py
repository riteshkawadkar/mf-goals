"""Unit tests for engine/allocate.py"""
import pytest
from app.engine.allocate import run_allocation
from tests.conftest import TODAY, make_holding, make_goal, make_assumptions


def _total_for_goal(results, goal_id):
    return sum(r.amount for r in results if r.goal_id == goal_id)


def _total_unallocated(results):
    return sum(r.amount for r in results if r.goal_id is None)


def _pct_sum_for_holding(results, holding_id):
    return sum(r.percentage for r in results if r.holding_id == holding_id)


def test_reconciles_to_100_per_holding():
    holdings = [
        make_holding("h1", "Liquid", "liquid", 0.0, "liquid", 100000, sigma=0.01),
        make_holding("h2", "Equity", "equity", 1.0, "flexi_cap", 200000, sigma=0.18),
    ]
    goals = [
        make_goal("g1", "Emergency", "emergency", target_today=80000,
                  equity_band_low=0.0, equity_band_high=0.0),
        make_goal("g2", "Retirement", "retirement", target_today=500000,
                  horizon_years=15.0, equity_band_low=0.70, equity_band_high=0.90),
    ]
    results = run_allocation(holdings, goals, [], [], make_assumptions(), TODAY)

    for h in holdings:
        total = _pct_sum_for_holding(results, h.id)
        assert abs(total - 100.0) < 0.1, f"Holding {h.id} sums to {total}% not 100%"


def test_total_amount_equals_portfolio_value():
    holdings = [
        make_holding("h1", "Liquid", "liquid", 0.0, "liquid", 100000, sigma=0.01),
        make_holding("h2", "Equity", "equity", 1.0, "flexi_cap", 200000),
    ]
    goals = [
        make_goal("g1", "Emergency", "emergency", target_today=80000,
                  equity_band_low=0.0, equity_band_high=0.0),
    ]
    results = run_allocation(holdings, goals, [], [], make_assumptions(), TODAY)
    total = sum(r.amount for r in results)
    assert abs(total - 300000) < 1.0


def test_unsuitable_asset_not_forced():
    # Small cap only, near-term goal — should end up unallocated
    holdings = [make_holding("h1", "Small Cap", "equity", 1.0, "small_cap", 100000, sigma=0.24)]
    goals = [make_goal("g1", "Near Term", "near_term_fixed", target_today=50000,
                       horizon_years=1.5, equity_band_low=0.0, equity_band_high=0.15)]
    results = run_allocation(holdings, goals, [], [], make_assumptions(), TODAY)
    goal_alloc = _total_for_goal(results, "g1")
    unalloc = _total_unallocated(results)
    assert goal_alloc == 0.0, "High-sigma equity should not be forced to near-term goal"
    assert unalloc > 0


def test_user_locks_respected():
    from app.engine.allocate import LockedEarmark
    holdings = [
        make_holding("h1", "Equity", "equity", 1.0, "flexi_cap", 100000),
        make_holding("h2", "Liquid", "liquid", 0.0, "liquid", 50000, sigma=0.01),
    ]
    goals = [
        make_goal("g1", "Emergency", "emergency", target_today=40000,
                  equity_band_low=0.0, equity_band_high=0.0),
        make_goal("g2", "Retirement", "retirement", target_today=200000,
                  horizon_years=15.0, equity_band_low=0.70, equity_band_high=0.90),
    ]
    # User has locked 100% of h1 to Retirement
    locked = [LockedEarmark(holding_id="h1", goal_id="g2", percentage=100.0, earmark_id="e1")]
    results = run_allocation(holdings, goals, [], locked, make_assumptions(), TODAY)

    # h1 should be entirely for g2
    h1_to_g2 = sum(r.amount for r in results if r.holding_id == "h1" and r.goal_id == "g2")
    assert abs(h1_to_g2 - 100000) < 1.0


def test_over_funded_goal_releases_surplus():
    a = make_assumptions(**{"overfunding.threshold": 1.20})
    holdings = [make_holding("h1", "Equity", "equity", 1.0, "flexi_cap", 1_000_000)]
    goals = [
        make_goal("g1", "Education", "education", target_today=100000,
                  horizon_years=10.0, equity_band_low=0.30, equity_band_high=0.60),
    ]
    results = run_allocation(holdings, goals, [], [], a, TODAY)
    unalloc = _total_unallocated(results)
    # With 1M portfolio and 100K target, lots should be unallocated
    assert unalloc > 0, "Surplus should be released to unallocated"
