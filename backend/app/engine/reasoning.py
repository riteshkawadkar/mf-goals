"""Build ReasoningObjects for every engine output.

Pure functions, no I/O. Each function returns a dict matching the
ReasoningObject schema. The runner writes them to the DB.
"""
from __future__ import annotations
import uuid
from datetime import date
from typing import Optional

from app.engine.eligibility import HoldingData, GoalData
from app.engine.allocate import EarmarkResult
from app.engine.diagnose import (
    DiagnosisResult, StructuralFlagResult, SufficiencyResult, PathSafetyResult
)


def _new_id() -> str:
    return str(uuid.uuid4())


def make_earmark_reasoning(
    result: EarmarkResult,
    holding: HoldingData,
    goal: Optional[GoalData],
    assumptions: dict[str, float],
) -> dict:
    if goal is None:
        plain = (
            f"{holding.scheme_name} has {result.percentage:.1f}% (₹{result.amount:,.0f}) "
            "placed in the Unallocated bucket. Under your assumptions, no active goal was "
            "eligible or had remaining demand for these funds."
        )
        rule_id = "earmark.unallocated"
        subject_ref = f"holding:{holding.id}"
    else:
        plain = (
            f"{result.percentage:.1f}% of {holding.scheme_name} (₹{result.amount:,.0f}) "
            f"is tracked toward '{goal.name}'. "
            f"This holding's asset class ({holding.asset_class}, equity fraction "
            f"{holding.equity_fraction:.0%}) is compatible with {goal.name}'s "
            f"equity band of {goal.equity_band_low:.0%}–{goal.equity_band_high:.0%}."
        )
        rule_id = "earmark.mcf_allocator"
        subject_ref = f"earmark:{result.holding_id}:{result.goal_id}"

    return {
        "id": _new_id(),
        "type": "earmark",
        "subject_ref": subject_ref,
        "rule_id": rule_id,
        "inputs_used": {
            "holding_id": holding.id,
            "goal_id": result.goal_id,
            "percentage": result.percentage,
            "amount": result.amount,
            "asset_class": holding.asset_class,
            "equity_fraction": holding.equity_fraction,
            "mu": holding.mu,
            "sigma": holding.sigma,
        },
        "assumptions_referenced": [
            f"band.{_horizon_key(goal)}.low" if goal else "",
            f"band.{_horizon_key(goal)}.high" if goal else "",
            f"mu.{_cluster_key(holding.style_cluster_id)}",
            f"sigma.{_cluster_key(holding.style_cluster_id)}",
        ],
        "plain_language": plain,
    }


def make_sufficiency_reasoning(
    goal: GoalData,
    result: SufficiencyResult,
    earmarked_value: float,
    assumptions: dict[str, float],
    today: date,
) -> dict:
    plain = (
        f"Under your assumptions, {goal.name} is '{result.verdict}'. "
        f"Projecting the ₹{earmarked_value:,.0f} earmarked corpus over "
        f"{_years_str(goal.horizon_date, today)} at the earmarked blend's "
        f"expected return gives a range of ₹{result.p10:,.0f} (pessimistic) "
        f"to ₹{result.p90:,.0f} (optimistic), with a central estimate of "
        f"₹{result.p50:,.0f}. The target is ₹{result.target_future_value:,.0f}. "
        f"Essential goals are judged against the pessimistic (p10) scenario; "
        f"this goal is judged against {result.judged_against}. "
        f"{result.illustrative_note}"
    )
    return {
        "id": _new_id(),
        "type": "sufficiency",
        "subject_ref": f"goal:{goal.id}",
        "rule_id": f"sufficiency.{result.verdict}",
        "inputs_used": {
            "goal_id": goal.id,
            "earmarked_value": earmarked_value,
            "p10": result.p10,
            "p50": result.p50,
            "p90": result.p90,
            "target_future_value": result.target_future_value,
            "judged_against": result.judged_against,
        },
        "assumptions_referenced": ["mu.diversified_equity", "sigma.diversified_equity"],
        "plain_language": plain,
    }


def make_path_safety_reasoning(goal: GoalData, result: PathSafetyResult) -> dict:
    scenario_lines = "; ".join(
        f"{s.scenario}: ₹{s.resulting_value:,.0f} ({'breaks goal' if s.breaks_goal else 'holds'})"
        for s in result.scenarios
    )
    plain = (
        f"{goal.name} has '{result.fragility}' fragility under named market stress scenarios. "
        f"Scenario outcomes: {scenario_lines}. "
        "These are illustrative shocks applied to the earmarked portfolio's equity fraction, "
        "not predictions."
    )
    return {
        "id": _new_id(),
        "type": "path_safety",
        "subject_ref": f"goal:{goal.id}",
        "rule_id": f"path_safety.{result.fragility}",
        "inputs_used": {
            "goal_id": goal.id,
            "fragility": result.fragility,
            "scenarios": [
                {"scenario": s.scenario, "shock": s.equity_shock, "breaks_goal": s.breaks_goal}
                for s in result.scenarios
            ],
        },
        "assumptions_referenced": ["stress.moderate", "stress.severe", "stress.gfc_2008", "stress.covid"],
        "plain_language": plain,
    }


def make_structural_flag_reasoning(flag: StructuralFlagResult, subject_ref: str) -> dict:
    return {
        "id": _new_id(),
        "type": "flag",
        "subject_ref": subject_ref,
        "rule_id": flag.rule_id,
        "inputs_used": {"flag_type": flag.type, "severity": flag.severity},
        "assumptions_referenced": [],
        "plain_language": flag.plain_language,
    }


def make_portfolio_health_reasoning(
    summary_line: str,
    aggregate_mix: dict,
    flags: list[StructuralFlagResult],
) -> dict:
    plain = (
        f"Portfolio Health summary: {summary_line}. "
        f"Aggregate mix — equity: {aggregate_mix['equity']:.0%}, "
        f"debt: {aggregate_mix['debt']:.0%}, "
        f"liquid: {aggregate_mix['liquid']:.0%}. "
    )
    if flags:
        plain += f"Flags: {'; '.join(f.plain_language for f in flags)}"
    return {
        "id": _new_id(),
        "type": "portfolio_health",
        "subject_ref": "portfolio",
        "rule_id": "portfolio_health.layer0",
        "inputs_used": {"aggregate_mix": aggregate_mix, "flag_count": len(flags)},
        "assumptions_referenced": [
            "concentration.amc_warn", "concentration.style_warn",
            "concentration.sector_warn", "emergency.min_months",
        ],
        "plain_language": plain,
    }


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _horizon_key(goal: Optional[GoalData]) -> str:
    if goal is None or goal.horizon_date is None:
        return "gt15y"
    from app.engine.eligibility import _years
    from datetime import date
    y = _years(goal.horizon_date, date.today())
    if y < 3:
        return "lt3y"
    elif y < 7:
        return "3_7y"
    elif y < 10:
        return "7_10y"
    elif y < 15:
        return "10_15y"
    return "gt15y"


def _cluster_key(style_cluster_id: str) -> str:
    mapping = {
        "liquid": "liquid",
        "short_debt": "short_debt",
        "medium_debt": "short_debt",
        "long_debt": "short_debt",
        "credit_debt": "short_debt",
        "hybrid": "hybrid",
        "small_cap": "small_cap",
        "international": "international",
    }
    return mapping.get(style_cluster_id, "diversified_equity")


def _years_str(horizon_date: Optional[date], today: date) -> str:
    if horizon_date is None:
        return "an open horizon"
    from app.engine.eligibility import _years
    y = _years(horizon_date, today)
    return f"{y:.1f} years"
