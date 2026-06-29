"""Layer 2 helpers – eligibility matrix.

Determines whether a holding (or specific tax lot) may be earmarked toward a goal.
Pure functions, no I/O.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


HIGH_SIGMA_THRESHOLD = 0.20   # sigma above this = "high volatility"
HIGH_EQUITY_THRESHOLD = 0.50  # equity_fraction above this = equity-heavy

# Minimum liquid fraction to satisfy emergency goal
EMERGENCY_LIQUID_ONLY = True


@dataclass
class HoldingData:
    id: str
    scheme_code: str
    scheme_name: str
    amc: str
    category: str
    asset_class: str          # liquid | debt | hybrid | equity
    equity_fraction: float
    style_cluster_id: str
    sector_tags: list[str]
    current_units: float
    current_nav: float
    current_value: float
    mu: float
    sigma: float
    tax_lots: list["TaxLotData"] = field(default_factory=list)


@dataclass
class TaxLotData:
    id: str
    holding_id: str
    units: float
    nav_at_buy: float
    cost_basis: float
    buy_date: date
    lock_until: Optional[date]
    gain_type: str            # stcg | ltcg | locked


@dataclass
class GoalData:
    id: str
    user_id: str
    name: str
    archetype: str
    target_today: Optional[float]
    horizon_date: Optional[date]
    priority: int
    inflation_rate: float
    target_future_value: Optional[float]
    confidence_tag: str
    equity_band_low: float
    equity_band_high: float
    glide_start_date: Optional[date]
    is_perpetual: bool


@dataclass
class SipData:
    scheme_code: str
    amount: float
    cadence: str    # monthly | quarterly
    run_until: Optional[date]


def is_lot_locked_before(lot: TaxLotData, horizon_date: Optional[date]) -> bool:
    """True if the lot is ELSS-locked past the goal's horizon."""
    if lot.lock_until is None:
        return False
    if horizon_date is None:
        return False
    return lot.lock_until > horizon_date


def unlocked_value(holding: HoldingData, horizon_date: Optional[date], today: date) -> float:
    """Return the portion of holding value that is NOT locked past horizon."""
    if not holding.tax_lots:
        return holding.current_value
    unlocked_units = sum(
        lot.units
        for lot in holding.tax_lots
        if not is_lot_locked_before(lot, horizon_date)
    )
    total_units = sum(lot.units for lot in holding.tax_lots)
    if total_units == 0:
        return 0.0
    return holding.current_value * (unlocked_units / total_units)


def is_eligible(holding: HoldingData, goal: GoalData, today: date) -> bool:
    """
    Determine whether a holding is eligible to fund a goal.

    Hard constraint rules (any False → ineligible):
    1. Emergency goal → only liquid assets (asset_class == 'liquid').
    2. Sub-3-year goal → equity_fraction must be < HIGH_EQUITY_THRESHOLD.
    3. High-sigma holding (sigma > HIGH_SIGMA_THRESHOLD) → ineligible for goals
       with horizon < 5 years.
    4. If ALL tax lots are ELSS-locked past horizon → ineligible.
    """
    horizon_years = _years(goal.horizon_date, today) if goal.horizon_date else None

    # Rule 1: emergency only takes liquid
    if goal.archetype == "emergency":
        if holding.asset_class != "liquid":
            return False
        # Also check not locked
        if horizon_years is not None and horizon_years < 0.25:
            if unlocked_value(holding, goal.horizon_date, today) == 0:
                return False
        return True

    # Rule 2: sub-3y goals cannot hold high equity
    if horizon_years is not None and horizon_years < 3.0:
        if holding.equity_fraction >= HIGH_EQUITY_THRESHOLD:
            return False

    # Rule 3: high-sigma ineligible for sub-5y goals
    if horizon_years is not None and horizon_years < 5.0:
        if holding.sigma >= HIGH_SIGMA_THRESHOLD:
            return False

    # Rule 4: all lots locked past horizon
    if goal.horizon_date is not None and holding.tax_lots:
        all_locked = all(is_lot_locked_before(lot, goal.horizon_date) for lot in holding.tax_lots)
        if all_locked:
            return False

    return True


def eligible_value(holding: HoldingData, goal: GoalData, today: date) -> float:
    """Value of holding eligible to serve this goal (respects lot-level ELSS locks)."""
    if not is_eligible(holding, goal, today):
        return 0.0
    if goal.horizon_date is None:
        return holding.current_value
    return unlocked_value(holding, goal.horizon_date, today)


def _years(d: Optional[date], today: date) -> float:
    if d is None:
        return 0.0
    return max(0.0, (d - today).days / 365.25)
