"""Shared test fixtures."""
import pytest
from datetime import date, timedelta
from app.engine.eligibility import HoldingData, TaxLotData, GoalData, SipData
from app.engine.allocate import LockedEarmark
from app.engine.characterize import _CONFIDENCE_MAP
from app.seeds.default_assumptions import DEFAULTS


TODAY = date(2026, 6, 29)


def make_assumptions(**overrides) -> dict:
    a = dict(DEFAULTS)
    a.update(overrides)
    return a


def make_holding(
    id: str,
    scheme_name: str,
    asset_class: str,
    equity_fraction: float,
    style_cluster_id: str,
    current_value: float,
    sigma: float = 0.18,
    mu: float = 0.12,
    amc: str = "TestAMC",
    category: str = "Test Category",
    sector_tags: list = None,
    tax_lots: list = None,
) -> HoldingData:
    return HoldingData(
        id=id,
        scheme_code=f"SC{id}",
        scheme_name=scheme_name,
        amc=amc,
        category=category,
        asset_class=asset_class,
        equity_fraction=equity_fraction,
        style_cluster_id=style_cluster_id,
        sector_tags=sector_tags or [],
        current_units=current_value / 100,  # assume NAV=100
        current_nav=100.0,
        current_value=current_value,
        mu=mu,
        sigma=sigma,
        tax_lots=tax_lots or [],
    )


def make_goal(
    id: str,
    name: str,
    archetype: str,
    target_today: float = None,
    horizon_years: float = None,
    priority: int = 1,
    equity_band_low: float = 0.0,
    equity_band_high: float = 1.0,
    is_perpetual: bool = False,
    inflation_rate: float = 0.06,
) -> GoalData:
    horizon_date = None
    if horizon_years is not None:
        horizon_date = TODAY + timedelta(days=int(horizon_years * 365))

    target_fv = None
    if target_today and horizon_date:
        years = (horizon_date - TODAY).days / 365.25
        target_fv = target_today * ((1 + inflation_rate) ** years)

    return GoalData(
        id=id,
        user_id="test_user",
        name=name,
        archetype=archetype,
        target_today=target_today,
        horizon_date=horizon_date,
        priority=priority,
        inflation_rate=inflation_rate,
        target_future_value=target_fv,
        confidence_tag=_CONFIDENCE_MAP.get(archetype, "medium"),
        equity_band_low=equity_band_low,
        equity_band_high=equity_band_high,
        glide_start_date=None,
        is_perpetual=is_perpetual,
    )


def make_lot(
    id: str,
    holding_id: str,
    units: float,
    nav_at_buy: float = 100.0,
    buy_date: date = None,
    lock_until: date = None,
    gain_type: str = "ltcg",
) -> TaxLotData:
    return TaxLotData(
        id=id,
        holding_id=holding_id,
        units=units,
        nav_at_buy=nav_at_buy,
        cost_basis=units * nav_at_buy,
        buy_date=buy_date or date(2020, 1, 1),
        lock_until=lock_until,
        gain_type=gain_type,
    )
