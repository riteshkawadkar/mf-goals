"""Layer 1 – Characterize goals and holdings.

Pure functions, no I/O. All date math uses datetime.date.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional


ARCHETYPE_INFLATION_KEYS = {
    "emergency": "inflation.emergency",
    "near_term_fixed": "inflation.near_term_fixed",
    "education": "inflation.education",
    "retirement": "inflation.retirement",
    "perpetual_wealth": "inflation.perpetual_wealth",
    "recurring_liability": "inflation.recurring_liability",
}

# Archetypes with no defined completion horizon / no sufficiency concept
PERPETUAL_ARCHETYPES = {"perpetual_wealth"}

# Confidence tag logic by archetype
_CONFIDENCE_MAP = {
    "emergency": "high",
    "near_term_fixed": "high",
    "education": "medium",
    "recurring_liability": "medium",
    "retirement": "low",
    "perpetual_wealth": "low",
}

# Style cluster ids derived from AMFI category strings (lowercase match)
_STYLE_CLUSTER_MAP = {
    "large cap": "large_cap",
    "mid cap": "mid_cap",
    "small cap": "small_cap",
    "flexi cap": "flexi_cap",
    "multi cap": "multi_cap",
    "large & mid cap": "large_mid_cap",
    "elss": "elss",
    "index": "index",
    "international": "international",
    "liquid": "liquid",
    "overnight": "liquid",
    "money market": "liquid",
    "ultra short": "short_debt",
    "low duration": "short_debt",
    "short duration": "short_debt",
    "medium duration": "medium_debt",
    "long duration": "long_debt",
    "gilt": "long_debt",
    "corporate bond": "medium_debt",
    "credit risk": "credit_debt",
    "dynamic bond": "medium_debt",
    "hybrid": "hybrid",
    "balanced advantage": "hybrid",
    "equity savings": "hybrid",
    "arbitrage": "liquid",
    "conservative hybrid": "hybrid",
    "aggressive hybrid": "hybrid",
}

# Asset class and equity fraction defaults by style cluster
_CLUSTER_ASSET = {
    "large_cap": ("equity", 1.0),
    "mid_cap": ("equity", 1.0),
    "small_cap": ("equity", 1.0),
    "flexi_cap": ("equity", 1.0),
    "multi_cap": ("equity", 1.0),
    "large_mid_cap": ("equity", 1.0),
    "elss": ("equity", 1.0),
    "index": ("equity", 1.0),
    "international": ("equity", 1.0),
    "liquid": ("liquid", 0.0),
    "short_debt": ("debt", 0.0),
    "medium_debt": ("debt", 0.0),
    "long_debt": ("debt", 0.0),
    "credit_debt": ("debt", 0.0),
    "hybrid": ("hybrid", 0.45),
}

# Sector tags by AMFI category pattern
_SECTOR_TAGS_MAP = {
    "international": ["international"],
    "banking": ["banking"],
    "pharma": ["pharma"],
    "technology": ["technology"],
    "infrastructure": ["infrastructure"],
    "consumption": ["consumption"],
    "energy": ["energy"],
    "fmcg": ["fmcg"],
}


def classify_holding(category: str) -> tuple[str, float, str, list[str]]:
    """Return (asset_class, equity_fraction, style_cluster_id, sector_tags)."""
    cat_lower = category.lower()
    cluster = "flexi_cap"  # default
    for key, val in _STYLE_CLUSTER_MAP.items():
        if key in cat_lower:
            cluster = val
            break

    asset_class, equity_fraction = _CLUSTER_ASSET.get(cluster, ("equity", 1.0))

    sector_tags: list[str] = []
    for tag_key, tags in _SECTOR_TAGS_MAP.items():
        if tag_key in cat_lower:
            sector_tags = tags
            break

    return asset_class, equity_fraction, cluster, sector_tags


def get_mu_sigma(style_cluster_id: str, assumptions: dict[str, float]) -> tuple[float, float]:
    cluster_map = {
        "liquid": "liquid",
        "short_debt": "short_debt",
        "medium_debt": "short_debt",
        "long_debt": "short_debt",
        "credit_debt": "short_debt",
        "hybrid": "hybrid",
        "large_cap": "diversified_equity",
        "mid_cap": "diversified_equity",
        "flexi_cap": "diversified_equity",
        "multi_cap": "diversified_equity",
        "large_mid_cap": "diversified_equity",
        "index": "diversified_equity",
        "elss": "diversified_equity",
        "small_cap": "small_cap",
        "international": "international",
    }
    assumption_cluster = cluster_map.get(style_cluster_id, "diversified_equity")
    mu = assumptions.get(f"mu.{assumption_cluster}", 0.10)
    sigma = assumptions.get(f"sigma.{assumption_cluster}", 0.18)
    return mu, sigma


def years_to_date(target: date, today: date) -> float:
    return max(0.0, (target - today).days / 365.25)


def compute_equity_band(horizon_years: float, assumptions: dict[str, float]) -> tuple[float, float]:
    if horizon_years < 3:
        return assumptions.get("band.lt3y.low", 0.0), assumptions.get("band.lt3y.high", 0.15)
    elif horizon_years < 7:
        return assumptions.get("band.3_7y.low", 0.15), assumptions.get("band.3_7y.high", 0.50)
    elif horizon_years < 10:
        return assumptions.get("band.7_10y.low", 0.30), assumptions.get("band.7_10y.high", 0.60)
    elif horizon_years < 15:
        return assumptions.get("band.10_15y.low", 0.50), assumptions.get("band.10_15y.high", 0.75)
    else:
        return assumptions.get("band.gt15y.low", 0.70), assumptions.get("band.gt15y.high", 0.90)


def compute_glide_start(horizon_date: Optional[date], equity_band_high: float) -> Optional[date]:
    """Glide-path start: begin de-risking 5 years before horizon if equity band > 30%."""
    if horizon_date is None or equity_band_high <= 0.30:
        return None
    return date(horizon_date.year - 5, horizon_date.month, horizon_date.day)


def characterize_goal(
    name: str,
    archetype: str,
    target_today: Optional[float],
    horizon_date: Optional[date],
    priority: int,
    inflation_rate: Optional[float],
    assumptions: dict[str, float],
    today: date,
) -> dict:
    """Compute all derived fields for a goal. Returns a dict of derived attributes."""
    is_perpetual = archetype in PERPETUAL_ARCHETYPES

    # Inflation rate
    if inflation_rate is None:
        key = ARCHETYPE_INFLATION_KEYS.get(archetype, "inflation.retirement")
        inflation_rate = assumptions.get(key, 0.06)

    # Future value
    target_future_value: Optional[float] = None
    if target_today is not None and horizon_date is not None and not is_perpetual:
        years = years_to_date(horizon_date, today)
        target_future_value = target_today * ((1 + inflation_rate) ** years)

    # Equity band
    if archetype == "emergency":
        band_low, band_high = 0.0, 0.0  # emergency: only liquid, no equity
    elif is_perpetual or horizon_date is None:
        if is_perpetual:
            band_low = assumptions.get("band.gt15y.low", 0.70)
            band_high = assumptions.get("band.gt15y.high", 0.90)
        else:
            band_low, band_high = 0.0, 0.15  # no horizon: conservative default
    else:
        horizon_years = years_to_date(horizon_date, today)
        band_low, band_high = compute_equity_band(horizon_years, assumptions)

    glide_start = compute_glide_start(horizon_date, band_high)
    confidence_tag = _CONFIDENCE_MAP.get(archetype, "medium")

    return {
        "inflation_rate": inflation_rate,
        "target_future_value": target_future_value,
        "confidence_tag": confidence_tag,
        "equity_band_low": band_low,
        "equity_band_high": band_high,
        "glide_start_date": glide_start,
        "is_perpetual": is_perpetual,
    }
