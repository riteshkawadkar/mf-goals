"""Unit tests for engine/eligibility.py"""
import pytest
from datetime import date, timedelta
from app.engine.eligibility import is_eligible, eligible_value, unlocked_value
from tests.conftest import TODAY, make_holding, make_goal, make_lot


def test_emergency_only_accepts_liquid():
    liquid = make_holding("h1", "Liquid Fund", "liquid", 0.0, "liquid", 100000)
    equity = make_holding("h2", "Flexi Cap", "equity", 1.0, "flexi_cap", 100000)
    goal = make_goal("g1", "Emergency", "emergency", target_today=200000)

    assert is_eligible(liquid, goal, TODAY) is True
    assert is_eligible(equity, goal, TODAY) is False


def test_sub3y_goal_rejects_high_equity():
    equity = make_holding("h1", "Equity Fund", "equity", 1.0, "flexi_cap", 100000)
    debt = make_holding("h2", "Debt Fund", "debt", 0.0, "short_debt", 100000)
    goal = make_goal("g1", "House Down Payment", "near_term_fixed",
                     target_today=500000, horizon_years=2.0,
                     equity_band_low=0.0, equity_band_high=0.15)

    assert is_eligible(equity, goal, TODAY) is False
    assert is_eligible(debt, goal, TODAY) is True


def test_high_sigma_ineligible_for_sub5y():
    small_cap = make_holding("h1", "Small Cap", "equity", 1.0, "small_cap",
                             100000, sigma=0.24)
    goal = make_goal("g1", "Travel", "near_term_fixed", target_today=100000,
                     horizon_years=3.0)

    assert is_eligible(small_cap, goal, TODAY) is False


def test_elss_locked_lot_ineligible():
    lock_date = TODAY + timedelta(days=365)  # unlocks in 1 year
    lot = make_lot("l1", "h1", units=100, lock_until=lock_date, gain_type="locked")
    holding = make_holding("h1", "ELSS Fund", "equity", 1.0, "elss", 10000,
                           tax_lots=[lot])

    # Goal horizon is in 6 months — lot locked past horizon
    goal = make_goal("g1", "Short Goal", "near_term_fixed",
                     target_today=10000, horizon_years=0.5,
                     equity_band_low=0.0, equity_band_high=0.15)

    # ELSS is high equity fraction so already ineligible for sub-3y; double check
    assert is_eligible(holding, goal, TODAY) is False


def test_elss_unlocked_lot_eligible_long_goal():
    past_date = TODAY - timedelta(days=365 * 4)
    lock_date = past_date + timedelta(days=365 * 3)  # already unlocked
    lot = make_lot("l1", "h1", units=100, buy_date=past_date, lock_until=lock_date, gain_type="ltcg")
    holding = make_holding("h1", "ELSS Fund", "equity", 1.0, "elss", 10000,
                           tax_lots=[lot])
    goal = make_goal("g1", "Retirement", "retirement",
                     target_today=1000000, horizon_years=15.0,
                     equity_band_low=0.70, equity_band_high=0.90)

    assert is_eligible(holding, goal, TODAY) is True


def test_unlocked_value_partial_elss():
    future_lock = TODAY + timedelta(days=500)
    past_lock = TODAY - timedelta(days=100)  # already unlocked

    lot1 = make_lot("l1", "h1", units=60, lock_until=future_lock, gain_type="locked")
    lot2 = make_lot("l2", "h1", units=40, lock_until=past_lock, gain_type="ltcg")
    holding = make_holding("h1", "ELSS", "equity", 1.0, "elss", 10000,
                           tax_lots=[lot1, lot2])

    horizon = TODAY + timedelta(days=365)
    val = unlocked_value(holding, horizon, TODAY)
    # 40% unlocked → 4000 of 10000
    assert abs(val - 4000) < 1
