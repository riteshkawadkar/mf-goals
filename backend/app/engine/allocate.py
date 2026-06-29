"""Layer 2 – Most-Constrained-First earmarking allocator.

Pure functions, no I/O. Input: holdings, goals, locked earmarks, assumptions.
Output: list of EarmarkResult (holding_id, goal_id|None, percentage, amount).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.engine.eligibility import (
    HoldingData, GoalData, SipData, is_eligible, eligible_value, _years
)
from app.engine.characterize import get_mu_sigma


@dataclass
class LockedEarmark:
    holding_id: str
    goal_id: Optional[str]
    percentage: float
    earmark_id: str


@dataclass
class EarmarkResult:
    holding_id: str
    goal_id: Optional[str]    # None → Unallocated
    percentage: float
    amount: float
    rule_id: str = "mcf_allocator"


def _compute_goal_demand(
    goal: GoalData,
    sips: list[SipData],
    assumptions: dict[str, float],
    today: date,
) -> float:
    """Estimate present-value corpus needed from existing holdings to meet this goal.

    Approach:
      1. Target future value minus expected future-value of remaining SIPs.
      2. Discount that remainder back at a blended expected return to get PV demand.
    Returns 0 for perpetual goals (no defined target).
    """
    if goal.is_perpetual:
        return 0.0

    # Goals with no horizon (emergency) — demand is simply target_today
    if goal.horizon_date is None:
        return goal.target_today or 0.0

    if goal.target_future_value is None:
        return goal.target_today or 0.0

    T = _years(goal.horizon_date, today)
    if T <= 0:
        return goal.target_future_value

    # Blended mu: midpoint of equity band × equity mu + (1-mid) × debt mu
    mid_eq = (goal.equity_band_low + goal.equity_band_high) / 2
    mu_eq = assumptions.get("mu.diversified_equity", 0.12)
    mu_debt = assumptions.get("mu.short_debt", 0.07)
    blended_mu = mid_eq * mu_eq + (1 - mid_eq) * mu_debt

    # Expected FV of remaining SIPs (monthly PMT)
    sip_fv = 0.0
    for sip in sips:
        if sip.cadence == "monthly":
            periods = T * 12
            monthly_rate = blended_mu / 12
        else:
            periods = T * 4
            monthly_rate = blended_mu / 4

        run_until = sip.run_until
        if run_until is not None and run_until < goal.horizon_date:
            if sip.cadence == "monthly":
                periods = min(periods, _years(run_until, today) * 12)
            else:
                periods = min(periods, _years(run_until, today) * 4)

        if monthly_rate > 0 and periods > 0:
            sip_fv += sip.amount * (((1 + monthly_rate) ** periods - 1) / monthly_rate) * (1 + monthly_rate)

    corpus_needed_fv = max(0.0, goal.target_future_value - sip_fv)

    # PV of required corpus
    pv = corpus_needed_fv / ((1 + blended_mu) ** T)
    return pv


def run_allocation(
    holdings: list[HoldingData],
    goals: list[GoalData],
    sips: list[SipData],
    locked_earmarks: list[LockedEarmark],
    assumptions: dict[str, float],
    today: date,
) -> list[EarmarkResult]:
    """Main allocator. Returns one EarmarkResult per (holding, goal|None) pair."""

    # ── 1. Compute available supply per holding (deduct locked earmarks) ──────
    locked_by_holding: dict[str, list[LockedEarmark]] = {}
    for le in locked_earmarks:
        locked_by_holding.setdefault(le.holding_id, []).append(le)

    available: dict[str, float] = {}
    for h in holdings:
        locked_pct = sum(le.percentage for le in locked_by_holding.get(h.id, []))
        locked_pct = min(locked_pct, 100.0)
        available[h.id] = h.current_value * (1 - locked_pct / 100.0)

    # ── 2. Compute demand per goal ──────────────────────────────────────────
    # Goals sorted by priority (lower number = higher priority)
    sorted_goals = sorted(goals, key=lambda g: g.priority)
    demand: dict[str, float] = {
        g.id: _compute_goal_demand(g, sips, assumptions, today) for g in sorted_goals
    }

    # ── 3. Scarcity per holding (eligible_supply / eligible_demand) ─────────
    def eligible_supply(h: HoldingData) -> float:
        return min(available[h.id], sum(
            eligible_value(h, g, today) / h.current_value * available[h.id]
            if h.current_value > 0 else 0.0
            for g in sorted_goals
            if is_eligible(h, g, today)
        ))

    def eligible_demand_for(h: HoldingData) -> float:
        return sum(demand[g.id] for g in sorted_goals if is_eligible(h, g, today))

    scarcity: dict[str, float] = {}
    for h in holdings:
        ed = eligible_demand_for(h)
        es = eligible_supply(h)
        if ed > 0:
            scarcity[h.id] = es / ed
        else:
            scarcity[h.id] = float("inf")

    sorted_holdings = sorted(holdings, key=lambda h: scarcity[h.id])

    # ── 4. Fill goals from most-constrained holdings first ──────────────────
    remaining_demand = dict(demand)
    allocations: dict[tuple[str, Optional[str]], float] = {}  # (holding_id, goal_id) → amount

    # Pre-fill locked earmarks
    for le in locked_earmarks:
        h = next((h for h in holdings if h.id == le.holding_id), None)
        if h is None:
            continue
        amount = h.current_value * le.percentage / 100.0
        key = (le.holding_id, le.goal_id)
        allocations[key] = allocations.get(key, 0.0) + amount
        if le.goal_id and le.goal_id in remaining_demand:
            remaining_demand[le.goal_id] = max(0.0, remaining_demand[le.goal_id] - amount)

    for h in sorted_holdings:
        avail = available[h.id]
        if avail <= 1e-6:
            continue

        # Goals this holding is eligible for, with remaining demand
        eligible_goals = [
            g for g in sorted_goals
            if is_eligible(h, g, today) and remaining_demand.get(g.id, 0.0) > 1e-6
        ]

        if not eligible_goals:
            # All goes to Unallocated
            key = (h.id, None)
            allocations[key] = allocations.get(key, 0.0) + avail
            continue

        # Distribute proportionally to remaining demand
        total_eligible_demand = sum(remaining_demand[g.id] for g in eligible_goals)
        allocated_this_holding = 0.0

        # Tie-break: high-gain / high-sigma equity → longest-horizon goals first
        # Sort eligible goals by horizon descending (longest first) as tie-break
        eligible_goals_sorted = sorted(
            eligible_goals,
            key=lambda g: (g.horizon_date or date.max),
            reverse=True,
        )

        for g in eligible_goals_sorted:
            proportion = remaining_demand[g.id] / total_eligible_demand if total_eligible_demand > 0 else 0.0
            alloc = min(avail * proportion, remaining_demand[g.id])
            # For high-sigma equity, prefer long-horizon goals (already sorted)
            if h.sigma >= 0.18 and _years(g.horizon_date, today) < 7:
                alloc = 0.0  # soft preference: skip high-sigma for mid-range goals if long available
            alloc = max(0.0, alloc)
            key = (h.id, g.id)
            allocations[key] = allocations.get(key, 0.0) + alloc
            remaining_demand[g.id] = max(0.0, remaining_demand[g.id] - alloc)
            allocated_this_holding += alloc

        # Re-run for goals that were skipped due to high-sigma soft preference
        skipped = [g for g in eligible_goals_sorted if allocations.get((h.id, g.id), 0.0) == 0.0
                   and remaining_demand.get(g.id, 0.0) > 1e-6]
        remainder_avail = avail - allocated_this_holding
        if skipped and remainder_avail > 1e-6:
            total_skip_demand = sum(remaining_demand[g.id] for g in skipped)
            for g in skipped:
                proportion = remaining_demand[g.id] / total_skip_demand if total_skip_demand > 0 else 0.0
                alloc = min(remainder_avail * proportion, remaining_demand[g.id])
                key = (h.id, g.id)
                allocations[key] = allocations.get(key, 0.0) + alloc
                remaining_demand[g.id] = max(0.0, remaining_demand[g.id] - alloc)
                allocated_this_holding += alloc

        # Unallocated remainder
        unalloc = avail - allocated_this_holding
        if unalloc > 1e-6:
            key = (h.id, None)
            allocations[key] = allocations.get(key, 0.0) + unalloc

    # ── 5. Handle over-funded goals → release surplus ───────────────────────
    overfunding_threshold = assumptions.get("overfunding.threshold", 1.20)
    for g in sorted_goals:
        if demand[g.id] <= 0:
            continue
        total_earmarked = sum(
            amt for (h_id, g_id), amt in allocations.items() if g_id == g.id
        )
        if total_earmarked > demand[g.id] * overfunding_threshold:
            surplus = total_earmarked - demand[g.id]
            # Proportionally release surplus from each contributing holding
            for h in holdings:
                key = (h.id, g.id)
                if key not in allocations or allocations[key] <= 0:
                    continue
                fraction = allocations[key] / total_earmarked
                release = surplus * fraction
                allocations[key] = max(0.0, allocations[key] - release)
                unalloc_key = (h.id, None)
                allocations[unalloc_key] = allocations.get(unalloc_key, 0.0) + release

    # ── 6. Reconcile to 100% per holding (ensure everything allocated) ──────
    for h in holdings:
        total_allocated = sum(amt for (h_id, _), amt in allocations.items() if h_id == h.id)
        gap = h.current_value - total_allocated
        if gap > 1e-6:
            key = (h.id, None)
            allocations[key] = allocations.get(key, 0.0) + gap

    # ── 7. Convert to EarmarkResult list with percentages ───────────────────
    results: list[EarmarkResult] = []
    for (h_id, g_id), amount in allocations.items():
        if amount < 1e-6:
            continue
        h = next(hh for hh in holdings if hh.id == h_id)
        percentage = (amount / h.current_value * 100.0) if h.current_value > 0 else 0.0
        results.append(EarmarkResult(
            holding_id=h_id,
            goal_id=g_id,
            percentage=round(percentage, 6),
            amount=round(amount, 2),
        ))

    return results
