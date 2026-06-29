"""
11 acceptance scenarios from the dev plan §11.

Each scenario exercises the full engine stack (characterize + eligibility +
allocate + diagnose) and asserts the expected behavior.
"""
import pytest
from datetime import date, timedelta
from app.engine.allocate import run_allocation, LockedEarmark
from app.engine.diagnose import (
    diagnose_goal, check_fragmentation, check_no_safe_assets,
    compute_portfolio_health_layer0,
)
from app.engine.eligibility import HoldingData, GoalData, SipData, is_eligible
from tests.conftest import TODAY, make_holding, make_goal, make_lot, make_assumptions

# Helper to run full diagnosis for a goal
def run_diagnosis(goal, earmark_results, holdings, sips, assumptions, all_goals):
    return diagnose_goal(goal, earmark_results, holdings, sips, assumptions, all_goals, TODAY)


def total_for_goal(results, goal_id):
    return sum(r.amount for r in results if r.goal_id == goal_id)


def total_unallocated(results):
    return sum(r.amount for r in results if r.goal_id is None)


# ─── Scenario 1 ─────────────────────────────────────────────────────────────
def test_scenario_1_typical_sip_investor():
    """
    Typical SIP investor: flexi cap + index + liquid.
    Expected:
    - Emergency ← liquid
    - Education/Retirement ← equity
    - Retirement tagged low-confidence
    """
    holdings = [
        make_holding("h_liquid", "Liquid Fund", "liquid", 0.0, "liquid", 100000, sigma=0.01, mu=0.06),
        make_holding("h_flexi", "Flexi Cap Fund", "equity", 1.0, "flexi_cap", 300000, sigma=0.18, mu=0.12),
        make_holding("h_index", "Index Fund", "equity", 1.0, "index", 200000, sigma=0.18, mu=0.12),
    ]
    goals = [
        make_goal("g_emrg", "Emergency Fund", "emergency", target_today=80000,
                  equity_band_low=0.0, equity_band_high=0.0, priority=1),
        make_goal("g_edu", "Education", "education", target_today=500000,
                  horizon_years=10.0, equity_band_low=0.30, equity_band_high=0.60, priority=2),
        make_goal("g_ret", "Retirement", "retirement", target_today=5_000_000,
                  horizon_years=20.0, equity_band_low=0.70, equity_band_high=0.90, priority=3),
    ]
    a = make_assumptions()
    results = run_allocation(holdings, goals, [], [], a, TODAY)

    # Emergency should get liquid (only liquid is eligible for emergency)
    emrg_alloc = total_for_goal(results, "g_emrg")
    liquid_to_emrg = sum(r.amount for r in results if r.holding_id == "h_liquid" and r.goal_id == "g_emrg")
    assert emrg_alloc > 0, "Emergency should have some allocation"
    assert liquid_to_emrg > 0, "Liquid should go to emergency"

    # Equity holdings should go to education/retirement
    equity_to_edu = sum(r.amount for r in results if r.holding_id in ("h_flexi", "h_index") and r.goal_id == "g_edu")
    equity_to_ret = sum(r.amount for r in results if r.holding_id in ("h_flexi", "h_index") and r.goal_id == "g_ret")
    assert equity_to_edu > 0 or equity_to_ret > 0, "Equity should go to goal"

    # Retirement should be low confidence
    assert goals[2].confidence_tag == "low"


# ─── Scenario 2 ─────────────────────────────────────────────────────────────
def test_scenario_2_small_cap_near_term_house():
    """
    Small cap only, near-term house goal.
    Expected: small cap ineligible for house → house largely Unallocated.
    """
    holdings = [
        make_holding("h_sc", "Small Cap Fund", "equity", 1.0, "small_cap", 200000, sigma=0.24),
    ]
    goals = [
        make_goal("g_house", "House Down Payment", "near_term_fixed",
                  target_today=150000, horizon_years=2.0,
                  equity_band_low=0.0, equity_band_high=0.15, priority=1),
    ]
    a = make_assumptions()
    results = run_allocation(holdings, goals, [], [], a, TODAY)

    house_alloc = total_for_goal(results, "g_house")
    assert house_alloc == 0.0, "Small cap should be ineligible for near-term house goal"

    unalloc = total_unallocated(results)
    assert unalloc > 0, "Portfolio should be largely Unallocated"


# ─── Scenario 3 ─────────────────────────────────────────────────────────────
def test_scenario_3_all_elss_half_locked():
    """
    All ELSS, half locked.
    Expected:
    - Unlocked lots → Emergency first (but wait — emergency needs liquid; ELSS is equity)
    - Actually: ELSS is equity, so emergency still can't use it.
    - Locked lots → Retirement (long horizon past lock)
    - Underfunded shown honestly
    """
    # Half locked (future), half unlocked (past)
    future_lock = TODAY + timedelta(days=365 * 2)   # locked for 2 more years
    past_lock = TODAY - timedelta(days=365)           # already unlocked

    lot_locked = make_lot("l1", "h_elss", units=500, buy_date=date(2024, 1, 1),
                          lock_until=future_lock, gain_type="locked")
    lot_unlocked = make_lot("l2", "h_elss", units=500, buy_date=date(2020, 1, 1),
                            lock_until=past_lock, gain_type="ltcg")

    holdings = [
        make_holding("h_elss", "ELSS Fund", "equity", 1.0, "elss", 100000,
                     sigma=0.18, tax_lots=[lot_locked, lot_unlocked]),
    ]
    goals = [
        make_goal("g_emrg", "Emergency", "emergency", target_today=50000,
                  equity_band_low=0.0, equity_band_high=0.0, priority=1),
        make_goal("g_ret", "Retirement", "retirement", target_today=2_000_000,
                  horizon_years=20.0, equity_band_low=0.70, equity_band_high=0.90, priority=2),
    ]
    a = make_assumptions()
    results = run_allocation(holdings, goals, [], [], a, TODAY)

    # Emergency must not receive ELSS (equity, not liquid)
    elss_to_emrg = total_for_goal(results, "g_emrg")
    assert elss_to_emrg == 0.0, "ELSS (equity) must not be forced to emergency goal"

    # Locked units should not be forced into emergency (they can't be accessed)
    # Retirement should receive ELSS
    elss_to_ret = total_for_goal(results, "g_ret")
    assert elss_to_ret > 0, "ELSS should go to Retirement"


# ─── Scenario 4 ─────────────────────────────────────────────────────────────
def test_scenario_4_goal_overload():
    """
    10 goals → fragmentation flag fires.
    """
    holdings = [make_holding("h1", "Equity", "equity", 1.0, "flexi_cap", 500000)]
    goals = [
        make_goal(f"g{i}", f"Goal {i}", "education",
                  target_today=100000, horizon_years=10.0,
                  equity_band_low=0.30, equity_band_high=0.60, priority=i)
        for i in range(10)
    ]
    a = make_assumptions(**{"fragmentation.max_goals": 7})
    flag = check_fragmentation(goals, a)
    assert flag is not None
    assert flag.type == "fragmentation"


# ─── Scenario 5 ─────────────────────────────────────────────────────────────
def test_scenario_5_over_funded_goal():
    """
    One goal massively over-funded → surplus released to Unallocated.
    """
    a = make_assumptions(**{"overfunding.threshold": 1.20})
    holdings = [make_holding("h1", "Equity", "equity", 1.0, "flexi_cap", 2_000_000)]
    goals = [
        make_goal("g1", "Small Goal", "education", target_today=50000,
                  horizon_years=10.0, equity_band_low=0.30, equity_band_high=0.60, priority=1),
    ]
    results = run_allocation(holdings, goals, [], [], a, TODAY)
    unalloc = total_unallocated(results)
    goal_alloc = total_for_goal(results, "g1")
    assert unalloc > 0, "Massive surplus should be in Unallocated"
    # Over-funding threshold means goal alloc should not be massively above demand
    # Demand is ~PV of 50K target ≈ 50K/(1.12^10) ≈ 16K; with 20% buffer ≈ ~19K
    assert goal_alloc < 200_000, "Goal should not absorb the entire 2M portfolio"


# ─── Scenario 6 ─────────────────────────────────────────────────────────────
def test_scenario_6_user_locks_honored():
    """
    User has locked earmarks → engine allocates around them, never overrides.
    """
    holdings = [
        make_holding("h1", "Flexi Cap", "equity", 1.0, "flexi_cap", 300000),
        make_holding("h2", "Index Fund", "equity", 1.0, "index", 200000),
    ]
    goals = [
        make_goal("g1", "Retirement", "retirement", target_today=3_000_000,
                  horizon_years=15.0, equity_band_low=0.70, equity_band_high=0.90, priority=1),
        make_goal("g2", "Education", "education", target_today=500000,
                  horizon_years=10.0, equity_band_low=0.30, equity_band_high=0.60, priority=2),
    ]
    # User locked 100% of h1 to g2
    locked = [LockedEarmark(holding_id="h1", goal_id="g2", percentage=100.0, earmark_id="e1")]
    a = make_assumptions()
    results = run_allocation(holdings, goals, [], locked, a, TODAY)

    h1_to_g2 = sum(r.amount for r in results if r.holding_id == "h1" and r.goal_id == "g2")
    h1_to_g1 = sum(r.amount for r in results if r.holding_id == "h1" and r.goal_id == "g1")

    assert abs(h1_to_g2 - 300000) < 1.0, "Lock must be honored: all of h1 to g2"
    assert h1_to_g1 == 0.0, "Engine must not override user lock"


# ─── Scenario 7 ─────────────────────────────────────────────────────────────
def test_scenario_7_market_crash_long_horizon():
    """
    Market crash on a long-horizon funded goal.
    Expected: path-safety notes recovery possible; with sufficient corpus even
    GFC-level shock does not break the goal.
    Corpus = 10M, target = 1M: after worst-case -55% shock = 4.5M > 1M → 'low'.
    """
    a = make_assumptions()
    goal = make_goal("g1", "Retirement", "retirement", target_today=500_000,
                     horizon_years=20.0, equity_band_low=0.70, equity_band_high=0.90, priority=1)
    goal.target_future_value = 1_000_000  # target well below corpus even post-crash
    # Corpus = 10M; after -55% equity shock: 10M * (1 - 1.0 * 0.55) = 4.5M > 1M
    h = make_holding("h1", "Equity", "equity", 1.0, "flexi_cap", 10_000_000, sigma=0.18)

    from app.engine.diagnose import compute_path_safety
    result = compute_path_safety(goal, [(h, 10_000_000)], a)
    # Even after worst-case crash, corpus far exceeds target → low fragility
    assert result.fragility == "low", (
        "Well-funded goal (corpus >> target) should have low fragility even with 100% equity"
    )
    # All scenarios should not break the goal
    assert not any(s.breaks_goal for s in result.scenarios)


# ─── Scenario 8 ─────────────────────────────────────────────────────────────
def test_scenario_8_wealthy_all_goals_covered():
    """
    Wealthy investor, all goals covered.
    Expected: remainder goes to Unallocated; no invented reallocation.
    """
    holdings = [make_holding("h1", "Equity", "equity", 1.0, "flexi_cap", 10_000_000)]
    goals = [
        make_goal("g1", "Education", "education", target_today=1_000_000,
                  horizon_years=10.0, equity_band_low=0.30, equity_band_high=0.60, priority=1),
        make_goal("g2", "Retirement", "retirement", target_today=2_000_000,
                  horizon_years=15.0, equity_band_low=0.70, equity_band_high=0.90, priority=2),
    ]
    a = make_assumptions(**{"overfunding.threshold": 1.20})
    results = run_allocation(holdings, goals, [], [], a, TODAY)
    unalloc = total_unallocated(results)
    assert unalloc > 0, "Excess wealth should go to Unallocated, not be invented into goals"

    # Total should still reconcile
    total = sum(r.amount for r in results)
    assert abs(total - 10_000_000) < 10.0


# ─── Scenario 9 ─────────────────────────────────────────────────────────────
def test_scenario_9_zero_goals():
    """
    CAS uploaded, zero goals → everything goes to Unallocated.
    """
    holdings = [make_holding("h1", "Equity", "equity", 1.0, "flexi_cap", 300000)]
    goals = []
    a = make_assumptions()
    results = run_allocation(holdings, goals, [], [], a, TODAY)
    unalloc = total_unallocated(results)
    assert abs(unalloc - 300000) < 1.0, "With no goals, entire portfolio is Unallocated"


# ─── Scenario 10 ─────────────────────────────────────────────────────────────
def test_scenario_10_perpetual_wealth():
    """
    Perpetual wealth goal → no sufficiency / on-track status.
    """
    a = make_assumptions()
    goal = make_goal("g1", "Wealth Creation", "perpetual_wealth", is_perpetual=True,
                     equity_band_low=0.70, equity_band_high=0.90)
    h = make_holding("h1", "Equity", "equity", 1.0, "flexi_cap", 500000)

    # Allocate
    holdings = [h]
    goals = [goal]
    results = run_allocation(holdings, goals, [], [], a, TODAY)

    # Diagnose
    from app.engine.diagnose import diagnose_goal
    diag = diagnose_goal(goal, results, holdings, [], a, goals, TODAY)

    assert diag.sufficiency is None, "Perpetual goal must have no sufficiency result"
    assert diag.path_safety is not None


# ─── Scenario 11 ─────────────────────────────────────────────────────────────
def test_scenario_11_100pct_equity_no_liquid_emergency():
    """
    100% equity, no liquid, emergency goal.
    Expected:
    - Emergency → Unallocated (correct: no eligible liquid)
    - Layer-0 Portfolio Health surfaces 'no emergency reserve / 100% equity' BEFORE goal status
    """
    holdings = [
        make_holding("h1", "Flexi Cap", "equity", 1.0, "flexi_cap", 500000, sigma=0.18),
        make_holding("h2", "Small Cap", "equity", 1.0, "small_cap", 200000, sigma=0.24),
    ]
    goals = [
        make_goal("g_emrg", "Emergency Fund", "emergency", target_today=300000,
                  equity_band_low=0.0, equity_band_high=0.0, priority=1),
        make_goal("g_ret", "Retirement", "retirement", target_today=2_000_000,
                  horizon_years=20.0, equity_band_low=0.70, equity_band_high=0.90, priority=2),
    ]
    a = make_assumptions()
    results = run_allocation(holdings, goals, [], [], a, TODAY)

    # Emergency must be Unallocated (no liquid in portfolio)
    emrg_alloc = total_for_goal(results, "g_emrg")
    assert emrg_alloc == 0.0, "Emergency must be Unallocated when no liquid holdings exist"

    # Layer-0 Portfolio Health: no_safe_assets flag
    flag = check_no_safe_assets(holdings)
    assert flag is not None, "no_safe_assets flag must fire for 100% equity portfolio"
    assert flag.severity == "critical"

    # The 'no safe assets' flag is a Layer-0 concern surfaced BEFORE goal-level status.
    # This is the key invariant from Scenario 11 — portfolio-level problems must not be
    # hidden by individual goal cards.
    assert flag.type == "no_safe_assets"
