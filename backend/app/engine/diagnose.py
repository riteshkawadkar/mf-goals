"""Layer 3 – Three independent diagnoses per goal.

Pure functions, no I/O.
  1. Sufficiency: p10/p50/p90 terminal value projection (log-normal).
  2. Path-safety: named stress scenarios → per-goal fragility level.
  3. Structural flags: band mismatch, lock conflict, over-funding,
     concentration, emergency adequacy, fragmentation, no_safe_assets.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.engine.eligibility import HoldingData, GoalData, SipData, _years, eligible_value


STRESS_SCENARIOS = [
    ("moderate",  -0.20, "stress.moderate"),
    ("severe",    -0.35, "stress.severe"),
    ("gfc_2008",  -0.55, "stress.gfc_2008"),
    ("covid",     -0.38, "stress.covid"),
]

ESSENTIAL_ARCHETYPES = {"emergency", "near_term_fixed", "education", "recurring_liability"}
ASPIRATIONAL_ARCHETYPES = {"retirement", "perpetual_wealth"}

ILLUSTRATIVE_NOTE = (
    "These are illustrative scenarios based on historical return distributions, "
    "not predictions. Actual outcomes will differ."
)


@dataclass
class StressResult:
    scenario: str
    equity_shock: float
    resulting_value: float
    breaks_goal: bool


@dataclass
class SufficiencyResult:
    p10: float
    p50: float
    p90: float
    target_future_value: float
    verdict: str        # on_track | behind | ahead | not_assured
    judged_against: str  # p10 | p50
    illustrative_note: str


@dataclass
class PathSafetyResult:
    fragility: str      # low | moderate | high
    scenarios: list[StressResult]


@dataclass
class StructuralFlagResult:
    type: str
    severity: str       # info | warn | critical
    plain_language: str
    rule_id: str


@dataclass
class DiagnosisResult:
    goal_id: str
    sufficiency: Optional[SufficiencyResult]
    path_safety: PathSafetyResult
    structural_flags: list[StructuralFlagResult]
    earmarked_value: float
    resulting_equity_fraction: float


# ─── Sufficiency ─────────────────────────────────────────────────────────────

def _blended_mu_sigma(
    earmarked_holdings: list[tuple[HoldingData, float]],  # (holding, earmarked_amount)
    assumptions: dict[str, float],
) -> tuple[float, float]:
    """Weighted-average mu and sigma for the earmarked portfolio slice."""
    total = sum(amt for _, amt in earmarked_holdings)
    if total <= 0:
        return 0.08, 0.15
    mu_weighted = sum(h.mu * amt for h, amt in earmarked_holdings) / total
    # Portfolio sigma: simplified as weighted average (conservative; ignores correlation reduction)
    sigma_weighted = sum(h.sigma * amt for h, amt in earmarked_holdings) / total
    return mu_weighted, sigma_weighted


def compute_sufficiency(
    goal: GoalData,
    earmarked_holdings: list[tuple[HoldingData, float]],
    sips: list[SipData],
    assumptions: dict[str, float],
    today: date,
) -> Optional[SufficiencyResult]:
    """Return None for perpetual goals."""
    if goal.is_perpetual or goal.target_future_value is None or goal.horizon_date is None:
        return None

    T = _years(goal.horizon_date, today)
    target_fv = goal.target_future_value

    current_corpus = sum(amt for _, amt in earmarked_holdings)
    mu, sigma = _blended_mu_sigma(earmarked_holdings, assumptions)

    # Log-normal projection of current corpus
    drift = (mu - 0.5 * sigma ** 2) * T
    spread = sigma * math.sqrt(T) if T > 0 else 0.0

    # z-scores for p10 / p90
    Z10, Z90 = -1.2816, 1.2816

    corpus_p10 = current_corpus * math.exp(drift + Z10 * spread) if T > 0 else current_corpus
    corpus_p50 = current_corpus * math.exp(drift) if T > 0 else current_corpus
    corpus_p90 = current_corpus * math.exp(drift + Z90 * spread) if T > 0 else current_corpus

    # Add expected future value of remaining SIPs (deterministic at blended mu)
    sip_fv_contribution = _sip_fv(sips, goal, mu, today)
    p10 = corpus_p10 + sip_fv_contribution * 0.70  # conservative SIP FV for p10
    p50 = corpus_p50 + sip_fv_contribution
    p90 = corpus_p90 + sip_fv_contribution * 1.30  # optimistic for p90

    # Verdict
    judged_against = "p10" if goal.archetype in ESSENTIAL_ARCHETYPES else "p50"
    judged_value = p10 if judged_against == "p10" else p50

    if judged_value >= target_fv * 1.10:
        verdict = "ahead"
    elif judged_value >= target_fv * 0.90:
        verdict = "on_track"
    elif judged_value >= target_fv * 0.60:
        verdict = "behind"
    else:
        verdict = "not_assured"

    return SufficiencyResult(
        p10=round(p10, 2),
        p50=round(p50, 2),
        p90=round(p90, 2),
        target_future_value=round(target_fv, 2),
        verdict=verdict,
        judged_against=judged_against,
        illustrative_note=ILLUSTRATIVE_NOTE,
    )


def _sip_fv(sips: list[SipData], goal: GoalData, mu: float, today: date) -> float:
    """FV of confirmed SIPs that will contribute toward this goal's horizon."""
    if not sips or goal.horizon_date is None:
        return 0.0
    T_years = _years(goal.horizon_date, today)
    total_fv = 0.0
    for sip in sips:
        if sip.cadence == "monthly":
            n_periods = T_years * 12
            r = mu / 12
        else:
            n_periods = T_years * 4
            r = mu / 4

        if sip.run_until is not None and sip.run_until < goal.horizon_date:
            effective_years = _years(sip.run_until, today)
            n_periods = effective_years * (12 if sip.cadence == "monthly" else 4)

        if r > 0 and n_periods > 0:
            fv = sip.amount * (((1 + r) ** n_periods - 1) / r) * (1 + r)
        elif n_periods > 0:
            fv = sip.amount * n_periods
        else:
            fv = 0.0
        total_fv += fv
    return total_fv


# ─── Path Safety ─────────────────────────────────────────────────────────────

def compute_path_safety(
    goal: GoalData,
    earmarked_holdings: list[tuple[HoldingData, float]],
    assumptions: dict[str, float],
) -> PathSafetyResult:
    total_value = sum(amt for _, amt in earmarked_holdings)
    if total_value <= 0:
        eq_fraction = 0.0
    else:
        eq_fraction = sum(h.equity_fraction * amt for h, amt in earmarked_holdings) / total_value

    target_fv = goal.target_future_value or 0.0
    scenarios: list[StressResult] = []
    breaks_count = 0

    for scenario_name, default_shock, assumption_key in STRESS_SCENARIOS:
        shock = assumptions.get(assumption_key, default_shock)
        resulting_value = total_value * (1 + eq_fraction * shock)
        breaks = (target_fv > 0) and (resulting_value < target_fv)
        if breaks:
            breaks_count += 1
        scenarios.append(StressResult(
            scenario=scenario_name,
            equity_shock=shock,
            resulting_value=round(resulting_value, 2),
            breaks_goal=breaks,
        ))

    # Fragility: which scenarios break the goal
    severe_breaks = any(s.breaks_goal for s in scenarios if s.scenario in ("gfc_2008", "covid"))
    moderate_breaks = any(s.breaks_goal for s in scenarios if s.scenario in ("moderate", "severe"))

    if severe_breaks:
        fragility = "high"
    elif moderate_breaks:
        fragility = "moderate"
    else:
        fragility = "low"

    return PathSafetyResult(fragility=fragility, scenarios=scenarios)


# ─── Structural Flags ────────────────────────────────────────────────────────

def check_band_mismatch(
    goal: GoalData,
    resulting_equity_fraction: float,
) -> Optional[StructuralFlagResult]:
    low, high = goal.equity_band_low, goal.equity_band_high
    if resulting_equity_fraction < low - 0.05:
        return StructuralFlagResult(
            type="band_mismatch",
            severity="warn",
            plain_language=(
                f"Under your assumptions, {goal.name} has {resulting_equity_fraction:.0%} equity, "
                f"below the {low:.0%}–{high:.0%} target band for its horizon. "
                "The earmarked funds may grow more slowly than needed."
            ),
            rule_id="band_mismatch.too_low",
        )
    if resulting_equity_fraction > high + 0.05:
        return StructuralFlagResult(
            type="band_mismatch",
            severity="warn",
            plain_language=(
                f"Under your assumptions, {goal.name} has {resulting_equity_fraction:.0%} equity, "
                f"above the {low:.0%}–{high:.0%} target band for its horizon. "
                "The earmarked funds carry more volatility than is typical for this timeline."
            ),
            rule_id="band_mismatch.too_high",
        )
    return None


def check_lock_conflict(
    goal: GoalData,
    earmarked_holdings: list[tuple[HoldingData, float]],
    today: date,
) -> Optional[StructuralFlagResult]:
    if goal.horizon_date is None:
        return None
    from app.engine.eligibility import is_lot_locked_before
    locked_value = 0.0
    for h, amt in earmarked_holdings:
        for lot in h.tax_lots:
            if is_lot_locked_before(lot, goal.horizon_date):
                lot_value = (lot.units / sum(l.units for l in h.tax_lots)) * amt if h.tax_lots else 0.0
                locked_value += lot_value
    if locked_value > 1e-2:
        return StructuralFlagResult(
            type="lock_conflict",
            severity="warn",
            plain_language=(
                f"Some funds earmarked to {goal.name} include ELSS units that unlock after "
                f"{goal.horizon_date}. ₹{locked_value:,.0f} may not be accessible in time."
            ),
            rule_id="lock_conflict.elss_past_horizon",
        )
    return None


def check_over_funding(
    goal: GoalData,
    earmarked_value: float,
    assumptions: dict[str, float],
) -> Optional[StructuralFlagResult]:
    if goal.target_future_value is None or goal.target_future_value <= 0:
        return None
    threshold = assumptions.get("overfunding.threshold", 1.20)
    if earmarked_value > goal.target_future_value * threshold:
        return StructuralFlagResult(
            type="over_funding",
            severity="info",
            plain_language=(
                f"{goal.name} appears well-funded: the earmarked corpus exceeds the target "
                f"by more than {(threshold - 1) * 100:.0f}%. Surplus has been released to Unallocated."
            ),
            rule_id="over_funding.surplus_released",
        )
    return None


def check_emergency_adequacy(
    goals: list[GoalData],
    holdings: list[HoldingData],
    earmarks_by_goal: dict[str, list[tuple[HoldingData, float]]],
    assumptions: dict[str, float],
) -> Optional[StructuralFlagResult]:
    """Check at portfolio level — returned as a goal-level flag on the emergency goal."""
    emergency_goals = [g for g in goals if g.archetype == "emergency"]
    if not emergency_goals:
        return None
    eg = emergency_goals[0]
    earmarked = earmarks_by_goal.get(eg.id, [])
    earmarked_value = sum(amt for _, amt in earmarked)
    target = eg.target_today or 0.0
    if target > 0 and earmarked_value < target * 0.80:
        gap = target - earmarked_value
        return StructuralFlagResult(
            type="emergency_inadequate",
            severity="critical",
            plain_language=(
                f"The emergency reserve goal is short by approximately ₹{gap:,.0f}. "
                "Under your assumptions, the current liquid holdings are insufficient "
                "to cover the target emergency fund."
            ),
            rule_id="emergency_inadequate.gap",
        )
    return None


def check_fragmentation(
    goals: list[GoalData],
    assumptions: dict[str, float],
) -> Optional[StructuralFlagResult]:
    max_goals = int(assumptions.get("fragmentation.max_goals", 7))
    if len(goals) > max_goals:
        return StructuralFlagResult(
            type="fragmentation",
            severity="warn",
            plain_language=(
                f"You have {len(goals)} goals, which dilutes the earmarked corpus across many objectives. "
                "Consider focusing on fewer high-priority goals to improve sufficiency confidence."
            ),
            rule_id="fragmentation.too_many_goals",
        )
    return None


# ─── Portfolio-Level Concentration Flags (used by Layer 0) ───────────────────

def check_concentration_style(
    holdings: list[HoldingData],
    assumptions: dict[str, float],
) -> list[StructuralFlagResult]:
    threshold = assumptions.get("concentration.style_warn", 0.40)
    total = sum(h.current_value for h in holdings)
    if total <= 0:
        return []
    cluster_values: dict[str, float] = {}
    for h in holdings:
        cluster_values[h.style_cluster_id] = cluster_values.get(h.style_cluster_id, 0.0) + h.current_value
    flags = []
    for cluster, val in cluster_values.items():
        if val / total > threshold:
            flags.append(StructuralFlagResult(
                type="concentration_style",
                severity="warn",
                plain_language=(
                    f"Your portfolio has {val / total:.0%} in the '{cluster}' style cluster, "
                    f"above the {threshold:.0%} concentration threshold."
                ),
                rule_id="concentration.style",
            ))
    return flags


def check_concentration_amc(
    holdings: list[HoldingData],
    assumptions: dict[str, float],
) -> list[StructuralFlagResult]:
    threshold = assumptions.get("concentration.amc_warn", 0.40)
    total = sum(h.current_value for h in holdings)
    if total <= 0:
        return []
    amc_values: dict[str, float] = {}
    for h in holdings:
        amc_values[h.amc] = amc_values.get(h.amc, 0.0) + h.current_value
    flags = []
    for amc, val in amc_values.items():
        if val / total > threshold:
            flags.append(StructuralFlagResult(
                type="concentration_amc",
                severity="warn",
                plain_language=(
                    f"Your portfolio has {val / total:.0%} with {amc}, "
                    f"above the {threshold:.0%} single-AMC concentration threshold."
                ),
                rule_id="concentration.amc",
            ))
    return flags


def check_concentration_sector(
    holdings: list[HoldingData],
    assumptions: dict[str, float],
) -> list[StructuralFlagResult]:
    threshold = assumptions.get("concentration.sector_warn", 0.30)
    total = sum(h.current_value for h in holdings)
    if total <= 0:
        return []
    sector_values: dict[str, float] = {}
    for h in holdings:
        for tag in h.sector_tags:
            sector_values[tag] = sector_values.get(tag, 0.0) + h.current_value
    flags = []
    for sector, val in sector_values.items():
        if val / total > threshold:
            flags.append(StructuralFlagResult(
                type="concentration_sector",
                severity="warn",
                plain_language=(
                    f"Your portfolio has {val / total:.0%} exposure to the '{sector}' sector, "
                    f"above the {threshold:.0%} concentration threshold."
                ),
                rule_id="concentration.sector",
            ))
    return flags


def check_no_safe_assets(holdings: list[HoldingData]) -> Optional[StructuralFlagResult]:
    has_safe = any(h.asset_class in ("liquid", "debt") for h in holdings)
    if not has_safe:
        return StructuralFlagResult(
            type="no_safe_assets",
            severity="critical",
            plain_language=(
                "Your portfolio has no liquid or debt holdings. "
                "Under your assumptions, this means no stable buffer is available for near-term needs."
            ),
            rule_id="portfolio.no_safe_assets",
        )
    return None


# Alias used by tests and runner
def compute_portfolio_health_layer0(
    holdings: list[HoldingData],
    assumptions: dict[str, float],
) -> list["StructuralFlagResult"]:
    """Return portfolio-level structural flags (Layer 0 subset)."""
    flags = []
    flags.extend(check_concentration_style(holdings, assumptions))
    flags.extend(check_concentration_amc(holdings, assumptions))
    flags.extend(check_concentration_sector(holdings, assumptions))
    no_safe = check_no_safe_assets(holdings)
    if no_safe:
        flags.append(no_safe)
    return flags


# ─── Per-goal diagnosis orchestrator ─────────────────────────────────────────

def diagnose_goal(
    goal: GoalData,
    earmark_results: list,  # list of EarmarkResult from allocate.py
    holdings: list[HoldingData],
    sips: list[SipData],
    assumptions: dict[str, float],
    all_goals: list[GoalData],
    today: date,
) -> DiagnosisResult:
    # Build earmarked_holdings: list of (holding, earmarked_amount)
    holding_map = {h.id: h for h in holdings}
    earmarked_holdings: list[tuple[HoldingData, float]] = []
    for er in earmark_results:
        if er.goal_id == goal.id and er.holding_id in holding_map:
            earmarked_holdings.append((holding_map[er.holding_id], er.amount))

    earmarked_value = sum(amt for _, amt in earmarked_holdings)
    total_eq = sum(h.equity_fraction * amt for h, amt in earmarked_holdings)
    resulting_equity_fraction = (total_eq / earmarked_value) if earmarked_value > 0 else 0.0

    # 1. Sufficiency
    sufficiency = compute_sufficiency(goal, earmarked_holdings, sips, assumptions, today)

    # 2. Path safety
    path_safety = compute_path_safety(goal, earmarked_holdings, assumptions)

    # 3. Structural flags for this goal
    flags: list[StructuralFlagResult] = []

    bm = check_band_mismatch(goal, resulting_equity_fraction)
    if bm:
        flags.append(bm)

    lc = check_lock_conflict(goal, earmarked_holdings, today)
    if lc:
        flags.append(lc)

    of = check_over_funding(goal, earmarked_value, assumptions)
    if of:
        flags.append(of)

    return DiagnosisResult(
        goal_id=goal.id,
        sufficiency=sufficiency,
        path_safety=path_safety,
        structural_flags=flags,
        earmarked_value=earmarked_value,
        resulting_equity_fraction=resulting_equity_fraction,
    )
