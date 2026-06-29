"""Seed the default assumption set for a new user."""
from sqlalchemy.orm import Session
from app.models.db import Assumption

DEFAULTS: dict[str, float] = {
    # --- Inflation by archetype ---
    "inflation.education": 0.10,
    "inflation.healthcare": 0.08,
    "inflation.retirement": 0.06,
    "inflation.house": 0.06,
    "inflation.wedding": 0.06,
    "inflation.travel": 0.05,
    "inflation.emergency": 0.06,
    "inflation.near_term_fixed": 0.06,
    "inflation.recurring_liability": 0.06,
    "inflation.perpetual_wealth": 0.06,
    # --- Equity bands by horizon (years): low and high ---
    "band.lt3y.low": 0.00,
    "band.lt3y.high": 0.15,
    "band.3_7y.low": 0.15,
    "band.3_7y.high": 0.50,
    "band.7_10y.low": 0.30,
    "band.7_10y.high": 0.60,
    "band.10_15y.low": 0.50,
    "band.10_15y.high": 0.75,
    "band.gt15y.low": 0.70,
    "band.gt15y.high": 0.90,
    # --- Expected returns (mu) per asset cluster ---
    "mu.liquid": 0.06,
    "mu.short_debt": 0.07,
    "mu.hybrid": 0.09,
    "mu.diversified_equity": 0.12,
    "mu.small_cap": 0.13,
    "mu.international": 0.11,
    # --- Volatility (sigma) per asset cluster ---
    "sigma.liquid": 0.01,
    "sigma.short_debt": 0.03,
    "sigma.hybrid": 0.08,
    "sigma.diversified_equity": 0.18,
    "sigma.small_cap": 0.24,
    "sigma.international": 0.16,
    # --- Stress scenario shocks (equity fraction of shock applied) ---
    "stress.moderate": -0.20,
    "stress.severe": -0.35,
    "stress.gfc_2008": -0.55,
    "stress.covid": -0.38,
    # --- Concentration thresholds ---
    "concentration.amc_warn": 0.40,
    "concentration.style_warn": 0.40,
    "concentration.sector_warn": 0.30,
    # --- Emergency reserve: months of estimated expenses as fraction of portfolio ---
    "emergency.min_months": 6.0,
    # --- Fragmentation: flag if more than this many goals ---
    "fragmentation.max_goals": 7.0,
    # --- Over-funding threshold (ratio) ---
    "overfunding.threshold": 1.20,
}


def seed_defaults(user_id: str, db: Session) -> None:
    existing_keys = {
        a.key for a in db.query(Assumption.key).filter(Assumption.user_id == user_id)
    }
    new_assumptions = [
        Assumption(user_id=user_id, key=k, value=v, is_default=True)
        for k, v in DEFAULTS.items()
        if k not in existing_keys
    ]
    if new_assumptions:
        db.add_all(new_assumptions)
        db.commit()


def get_assumption_map(user_id: str, db: Session) -> dict[str, float]:
    rows = db.query(Assumption).filter(Assumption.user_id == user_id).all()
    result = dict(DEFAULTS)  # start with defaults
    for row in rows:
        result[row.key] = row.value
    return result
