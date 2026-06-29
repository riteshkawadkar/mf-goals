"""Engine runner — orchestrates Layers 0-3 and persists results.

This module has DB access (it reads and writes). The pure engine modules
(characterize, eligibility, allocate, diagnose, reasoning) are I/O-free.
"""
from __future__ import annotations
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.db import (
    Holding as DBHolding, TaxLot as DBTaxLot, Goal as DBGoal,
    ActiveSip as DBSip, Assumption, Earmark as DBEarmark,
    ReasoningObject as DBReasoningObject, Diagnosis as DBDiagnosis,
    DashboardSnapshot, NavCache, User,
)
from app.schemas.api import (
    DashboardState, PortfolioHealth, AggregateMix, Concentration,
    GoalView, Goal as GoalSchema, Earmark as EarmarkSchema,
    Diagnosis as DiagnosisSchema, SufficiencyResult as SuffSchema,
    PathSafetyResult as PSSchema, StressResult as SRSchema,
    StructuralFlag as SFSchema, ReasoningObject as ROSchema,
    UnallocatedBucket, DataFreshness, TaxLot as TaxLotSchema, Holding as HoldingSchema,
)
from app.engine.eligibility import HoldingData, TaxLotData, GoalData, SipData
from app.engine.characterize import classify_holding, get_mu_sigma
from app.engine.allocate import run_allocation, LockedEarmark, EarmarkResult
from app.engine.diagnose import (
    diagnose_goal, DiagnosisResult,
    check_concentration_style, check_concentration_amc, check_concentration_sector,
    check_no_safe_assets, check_emergency_adequacy, check_fragmentation,
    StructuralFlagResult,
)
from app.engine.reasoning import (
    make_earmark_reasoning, make_sufficiency_reasoning, make_path_safety_reasoning,
    make_structural_flag_reasoning, make_portfolio_health_reasoning,
)
from app.seeds.default_assumptions import get_assumption_map


# ─── Data loaders ────────────────────────────────────────────────────────────

def _load_holdings(user_id: str, db: Session, assumptions: dict[str, float]) -> list[HoldingData]:
    db_holdings = db.query(DBHolding).filter(DBHolding.user_id == user_id).all()
    results = []
    for h in db_holdings:
        nav_row = db.get(NavCache, h.scheme_code)
        current_nav = nav_row.nav if nav_row else 0.0
        current_value = h.current_units * current_nav

        asset_class, equity_fraction, style_cluster_id, sector_tags = classify_holding(h.category)
        # Prefer stored values (updated by CAS parse)
        asset_class = h.asset_class or asset_class
        equity_fraction = h.equity_fraction if h.equity_fraction is not None else equity_fraction
        style_cluster_id = h.style_cluster_id or style_cluster_id
        sector_tags = h.sector_tags or sector_tags

        mu, sigma = get_mu_sigma(style_cluster_id, assumptions)

        tax_lots = [
            TaxLotData(
                id=lot.id,
                holding_id=lot.holding_id,
                units=lot.units,
                nav_at_buy=lot.nav_at_buy,
                cost_basis=lot.cost_basis,
                buy_date=lot.buy_date,
                lock_until=lot.lock_until,
                gain_type=lot.gain_type,
            )
            for lot in h.tax_lots
        ]

        results.append(HoldingData(
            id=h.id,
            scheme_code=h.scheme_code,
            scheme_name=h.scheme_name,
            amc=h.amc,
            category=h.category,
            asset_class=asset_class,
            equity_fraction=equity_fraction,
            style_cluster_id=style_cluster_id,
            sector_tags=sector_tags,
            current_units=h.current_units,
            current_nav=current_nav,
            current_value=current_value,
            mu=mu,
            sigma=sigma,
            tax_lots=tax_lots,
        ))
    return results


def _load_goals(user_id: str, db: Session) -> list[GoalData]:
    db_goals = db.query(DBGoal).filter(DBGoal.user_id == user_id).order_by(DBGoal.priority).all()
    return [
        GoalData(
            id=g.id,
            user_id=g.user_id,
            name=g.name,
            archetype=g.archetype,
            target_today=g.target_today,
            horizon_date=g.horizon_date,
            priority=g.priority,
            inflation_rate=g.inflation_rate,
            target_future_value=g.target_future_value,
            confidence_tag=g.confidence_tag,
            equity_band_low=g.equity_band_low,
            equity_band_high=g.equity_band_high,
            glide_start_date=g.glide_start_date,
            is_perpetual=g.is_perpetual,
        )
        for g in db_goals
    ]


def _load_sips(user_id: str, db: Session) -> list[SipData]:
    db_sips = db.query(DBSip).filter(
        DBSip.user_id == user_id,
        DBSip.source == "confirmed",
    ).all()
    return [
        SipData(
            scheme_code=s.scheme_code,
            amount=s.amount,
            cadence=s.cadence,
            run_until=s.run_until,
        )
        for s in db_sips
    ]


def _load_locked_earmarks(user_id: str, db: Session) -> list[LockedEarmark]:
    rows = db.query(DBEarmark).filter(
        DBEarmark.user_id == user_id,
        DBEarmark.locked_by_user == True,
    ).all()
    return [
        LockedEarmark(
            holding_id=e.holding_id,
            goal_id=e.goal_id,
            percentage=e.percentage,
            earmark_id=e.id,
        )
        for e in rows
    ]


# ─── Layer 0: Portfolio Health ────────────────────────────────────────────────

def compute_portfolio_health(
    holdings: list[HoldingData],
    goals: list[GoalData],
    assumptions: dict[str, float],
) -> tuple[dict, list[StructuralFlagResult], str]:
    """Returns (aggregate_mix_dict, portfolio_flags, summary_line)."""
    total = sum(h.current_value for h in holdings)

    if total <= 0:
        mix = {"equity": 0.0, "debt": 0.0, "liquid": 0.0}
        return mix, [], "No holdings"

    equity_val = sum(h.current_value for h in holdings if h.asset_class == "equity")
    equity_val += sum(h.current_value * h.equity_fraction for h in holdings if h.asset_class == "hybrid")
    debt_val = sum(h.current_value for h in holdings if h.asset_class == "debt")
    debt_val += sum(h.current_value * (1 - h.equity_fraction) for h in holdings if h.asset_class == "hybrid")
    liquid_val = sum(h.current_value for h in holdings if h.asset_class == "liquid")

    mix = {
        "equity": round(equity_val / total, 4),
        "debt": round(debt_val / total, 4),
        "liquid": round(liquid_val / total, 4),
    }

    flags: list[StructuralFlagResult] = []
    flags.extend(check_concentration_style(holdings, assumptions))
    flags.extend(check_concentration_amc(holdings, assumptions))
    flags.extend(check_concentration_sector(holdings, assumptions))
    no_safe = check_no_safe_assets(holdings)
    if no_safe:
        flags.append(no_safe)

    # Summary line
    parts = []
    if mix["liquid"] < 0.05:
        parts.append("No meaningful liquid buffer")
    if mix["equity"] > 0.90:
        parts.append("100% equity")
    elif mix["equity"] > 0.70:
        parts.append("Equity-heavy")
    if not parts:
        parts.append(f"Equity {mix['equity']:.0%} | Debt {mix['debt']:.0%} | Liquid {mix['liquid']:.0%}")
    summary_line = " | ".join(parts)

    return mix, flags, summary_line


# ─── Persist helpers ─────────────────────────────────────────────────────────

def _upsert_reasoning(ro_dict: dict, user_id: str, db: Session) -> str:
    obj = DBReasoningObject(
        id=ro_dict["id"],
        user_id=user_id,
        type=ro_dict["type"],
        subject_ref=ro_dict["subject_ref"],
        rule_id=ro_dict["rule_id"],
        inputs_used=ro_dict["inputs_used"],
        assumptions_referenced=ro_dict["assumptions_referenced"],
        plain_language=ro_dict["plain_language"],
    )
    db.merge(obj)
    return ro_dict["id"]


def _persist_earmarks(
    user_id: str,
    earmark_results: list[EarmarkResult],
    reasoning_ids: dict[tuple[str, Optional[str]], str],
    db: Session,
) -> None:
    # Delete non-locked earmarks for this user
    db.query(DBEarmark).filter(
        DBEarmark.user_id == user_id,
        DBEarmark.locked_by_user == False,
    ).delete(synchronize_session=False)
    db.flush()

    for er in earmark_results:
        ro_id = reasoning_ids.get((er.holding_id, er.goal_id), "")
        obj = DBEarmark(
            user_id=user_id,
            holding_id=er.holding_id,
            goal_id=er.goal_id,
            percentage=er.percentage,
            locked_by_user=False,
            reasoning_object_id=ro_id if ro_id else None,
        )
        db.add(obj)


def _persist_diagnosis(
    user_id: str,
    goal: GoalData,
    diag: DiagnosisResult,
    reasoning_ids: dict,
    db: Session,
) -> None:
    existing = db.query(DBDiagnosis).filter(DBDiagnosis.goal_id == goal.id).first()
    suf = diag.sufficiency

    stress_results_json = [
        {
            "scenario": s.scenario,
            "equity_shock": s.equity_shock,
            "resulting_value": s.resulting_value,
            "breaks_goal": s.breaks_goal,
        }
        for s in diag.path_safety.scenarios
    ]
    flags_json = [
        {"type": f.type, "severity": f.severity, "plain_language": f.plain_language, "rule_id": f.rule_id}
        for f in diag.structural_flags
    ]

    if existing:
        existing.p10 = suf.p10 if suf else None
        existing.p50 = suf.p50 if suf else None
        existing.p90 = suf.p90 if suf else None
        existing.sufficiency_verdict = suf.verdict if suf else None
        existing.judged_against = suf.judged_against if suf else None
        existing.path_safety_fragility = diag.path_safety.fragility
        existing.structural_flags = flags_json
        existing.stress_results = stress_results_json
    else:
        db.add(DBDiagnosis(
            goal_id=goal.id,
            user_id=user_id,
            p10=suf.p10 if suf else None,
            p50=suf.p50 if suf else None,
            p90=suf.p90 if suf else None,
            sufficiency_verdict=suf.verdict if suf else None,
            judged_against=suf.judged_against if suf else None,
            path_safety_fragility=diag.path_safety.fragility,
            structural_flags=flags_json,
            stress_results=stress_results_json,
        ))


# ─── Schema builders ─────────────────────────────────────────────────────────

def _build_goal_schema(g: GoalData) -> GoalSchema:
    return GoalSchema(
        id=g.id,
        name=g.name,
        archetype=g.archetype,
        target_today=g.target_today,
        horizon_date=g.horizon_date,
        priority=g.priority,
        inflation_rate=g.inflation_rate,
        target_future_value=g.target_future_value,
        confidence_tag=g.confidence_tag,
        equity_band_low=g.equity_band_low,
        equity_band_high=g.equity_band_high,
        glide_start_date=g.glide_start_date,
        is_perpetual=g.is_perpetual,
    )


def _build_earmark_schema(er: EarmarkResult, reasoning_id: str, holding_map: dict) -> EarmarkSchema:
    return EarmarkSchema(
        id=str(er.holding_id) + ":" + str(er.goal_id),  # synthetic for now; replaced by DB id
        holding_id=er.holding_id,
        goal_id=er.goal_id,
        percentage=er.percentage,
        amount=er.amount,
        locked_by_user=False,
        reasoning_object_id=reasoning_id or "",
    )


def _flag_to_schema(flag: StructuralFlagResult, reasoning_id: str) -> SFSchema:
    return SFSchema(
        type=flag.type,
        severity=flag.severity,
        plain_language=flag.plain_language,
        reasoning_object_id=reasoning_id,
    )


def _build_diagnosis_schema(
    goal: GoalData,
    diag: DiagnosisResult,
    reasoning_ids: dict,
    portfolio_flags: list[StructuralFlagResult],
    all_goals: list[GoalData],
    assumptions: dict[str, float],
) -> DiagnosisSchema:
    suf = diag.sufficiency
    suf_schema = None
    if suf:
        suf_schema = SuffSchema(
            p10=suf.p10,
            p50=suf.p50,
            p90=suf.p90,
            target_future_value=suf.target_future_value,
            verdict=suf.verdict,
            judged_against=suf.judged_against,
            illustrative_note=suf.illustrative_note,
        )

    ps = diag.path_safety
    ps_schema = PSSchema(
        fragility=ps.fragility,
        scenarios=[
            SRSchema(
                scenario=s.scenario,
                equity_shock=s.equity_shock,
                resulting_value=s.resulting_value,
                breaks_goal=s.breaks_goal,
            )
            for s in ps.scenarios
        ],
    )

    struct_flags = []
    for flag in diag.structural_flags:
        ro_id = reasoning_ids.get(f"flag:{flag.rule_id}:{goal.id}", "")
        struct_flags.append(_flag_to_schema(flag, ro_id))

    return DiagnosisSchema(
        goal_id=goal.id,
        sufficiency=suf_schema,
        path_safety=ps_schema,
        structural_flags=struct_flags,
    )


# ─── Main engine run ─────────────────────────────────────────────────────────

def run_engine(user_id: str, db: Session) -> DashboardState:
    today = date.today()
    assumptions = get_assumption_map(user_id, db)

    # Load
    holdings = _load_holdings(user_id, db, assumptions)
    goals = _load_goals(user_id, db)
    sips = _load_sips(user_id, db)
    locked_earmarks = _load_locked_earmarks(user_id, db)

    # Delete all old (non-locked) reasoning objects for clean slate
    db.query(DBReasoningObject).filter(
        DBReasoningObject.user_id == user_id,
        DBReasoningObject.type.in_(["earmark", "sufficiency", "path_safety", "portfolio_health", "flag"]),
    ).delete(synchronize_session=False)
    db.flush()

    all_reasoning: list[dict] = []
    reasoning_ids: dict = {}

    # ── Layer 0: Portfolio Health ──────────────────────────────────────────
    mix, portfolio_flags, summary_line = compute_portfolio_health(holdings, goals, assumptions)

    ph_ro = make_portfolio_health_reasoning(summary_line, mix, portfolio_flags)
    ph_ro_id = _upsert_reasoning(ph_ro, user_id, db)
    all_reasoning.append(ph_ro)

    portfolio_flag_ros: list[SFSchema] = []
    for flag in portfolio_flags:
        flag_ro = make_structural_flag_reasoning(flag, "portfolio")
        _upsert_reasoning(flag_ro, user_id, db)
        all_reasoning.append(flag_ro)
        reasoning_ids[f"portfolio_flag:{flag.rule_id}"] = flag_ro["id"]
        portfolio_flag_ros.append(_flag_to_schema(flag, flag_ro["id"]))

    # Emergency reserve adequacy check
    emergency_goals = [g for g in goals if g.archetype == "emergency"]
    emergency_reserve_adequate = True
    if emergency_goals:
        eg = emergency_goals[0]
        liquid_val = sum(h.current_value for h in holdings if h.asset_class == "liquid")
        if eg.target_today and liquid_val < eg.target_today * 0.80:
            emergency_reserve_adequate = False

    liquid_total = sum(h.current_value for h in holdings if h.asset_class == "liquid")
    total_val = sum(h.current_value for h in holdings)
    liquidity_posture = (
        f"Liquid holdings: ₹{liquid_total:,.0f} ({liquid_total/total_val:.0%} of portfolio)"
        if total_val > 0 else "No holdings"
    )

    concentration_style = [
        _flag_to_schema(f, reasoning_ids.get(f"portfolio_flag:{f.rule_id}", ""))
        for f in portfolio_flags if f.type == "concentration_style"
    ]
    concentration_amc = [
        _flag_to_schema(f, reasoning_ids.get(f"portfolio_flag:{f.rule_id}", ""))
        for f in portfolio_flags if f.type == "concentration_amc"
    ]
    concentration_sector = [
        _flag_to_schema(f, reasoning_ids.get(f"portfolio_flag:{f.rule_id}", ""))
        for f in portfolio_flags if f.type == "concentration_sector"
    ]

    portfolio_health = PortfolioHealth(
        emergency_reserve_adequate=emergency_reserve_adequate,
        aggregate_mix=AggregateMix(**mix),
        concentration=Concentration(
            style=concentration_style,
            amc=concentration_amc,
            sector=concentration_sector,
        ),
        liquidity_posture=liquidity_posture,
        summary_line=summary_line,
        flags=[f for f in portfolio_flag_ros if f.type not in ("concentration_style", "concentration_amc", "concentration_sector")],
    )

    # ── Layer 2: Earmarking ────────────────────────────────────────────────
    earmark_results = run_allocation(holdings, goals, sips, locked_earmarks, assumptions, today)

    holding_map = {h.id: h for h in holdings}
    goal_map = {g.id: g for g in goals}

    earmark_reasoning_ids: dict[tuple[str, Optional[str]], str] = {}
    for er in earmark_results:
        h = holding_map[er.holding_id]
        g = goal_map.get(er.goal_id) if er.goal_id else None
        ro = make_earmark_reasoning(er, h, g, assumptions)
        ro_id = _upsert_reasoning(ro, user_id, db)
        all_reasoning.append(ro)
        earmark_reasoning_ids[(er.holding_id, er.goal_id)] = ro_id

    _persist_earmarks(user_id, earmark_results, earmark_reasoning_ids, db)

    # ── Layer 3: Diagnose + portfolio-level flags ──────────────────────────
    earmarks_by_goal: dict[str, list[tuple[HoldingData, float]]] = {}
    for er in earmark_results:
        if er.goal_id:
            earmarks_by_goal.setdefault(er.goal_id, []).append((holding_map[er.holding_id], er.amount))

    # Portfolio-level structural flags (fragmentation, emergency adequacy)
    frag_flag = check_fragmentation(goals, assumptions)
    em_flag = check_emergency_adequacy(goals, holdings, earmarks_by_goal, assumptions)
    for flag in [frag_flag, em_flag]:
        if flag:
            flag_ro = make_structural_flag_reasoning(flag, "portfolio")
            _upsert_reasoning(flag_ro, user_id, db)
            all_reasoning.append(flag_ro)
            reasoning_ids[f"portfolio_flag:{flag.rule_id}"] = flag_ro["id"]
            portfolio_flag_ros.append(_flag_to_schema(flag, flag_ro["id"]))
            portfolio_health.flags.append(_flag_to_schema(flag, flag_ro["id"]))

    goal_views: list[GoalView] = []
    for goal in goals:
        diag = diagnose_goal(goal, earmark_results, holdings, sips, assumptions, goals, today)

        # Reasoning for sufficiency
        if diag.sufficiency:
            suf_ro = make_sufficiency_reasoning(goal, diag.sufficiency, diag.earmarked_value, assumptions, today)
            suf_ro_id = _upsert_reasoning(suf_ro, user_id, db)
            all_reasoning.append(suf_ro)
            reasoning_ids[f"suf:{goal.id}"] = suf_ro_id

        # Reasoning for path safety
        ps_ro = make_path_safety_reasoning(goal, diag.path_safety)
        ps_ro_id = _upsert_reasoning(ps_ro, user_id, db)
        all_reasoning.append(ps_ro)
        reasoning_ids[f"ps:{goal.id}"] = ps_ro_id

        # Reasoning for structural flags
        for flag in diag.structural_flags:
            flag_ro = make_structural_flag_reasoning(flag, f"goal:{goal.id}")
            _upsert_reasoning(flag_ro, user_id, db)
            all_reasoning.append(flag_ro)
            reasoning_ids[f"flag:{flag.rule_id}:{goal.id}"] = flag_ro["id"]

        _persist_diagnosis(user_id, goal, diag, reasoning_ids, db)

        # Build earmark schemas for this goal (using actual DB ids after flush)
        db.flush()
        db_earmarks = db.query(DBEarmark).filter(
            DBEarmark.user_id == user_id,
            DBEarmark.goal_id == goal.id,
        ).all()
        earmark_schemas = [
            EarmarkSchema(
                id=e.id,
                holding_id=e.holding_id,
                goal_id=e.goal_id,
                percentage=e.percentage,
                amount=e.percentage / 100.0 * (holding_map[e.holding_id].current_value if e.holding_id in holding_map else 0),
                locked_by_user=e.locked_by_user,
                reasoning_object_id=e.reasoning_object_id or "",
            )
            for e in db_earmarks
        ]

        diag_schema = _build_diagnosis_schema(goal, diag, reasoning_ids, portfolio_flags, goals, assumptions)

        goal_views.append(GoalView(
            goal=_build_goal_schema(goal),
            earmarks=earmark_schemas,
            diagnosis=diag_schema,
            resulting_equity_fraction=round(diag.resulting_equity_fraction, 4),
        ))

    # ── Unallocated bucket ─────────────────────────────────────────────────
    db.flush()
    unalloc_earmarks = db.query(DBEarmark).filter(
        DBEarmark.user_id == user_id,
        DBEarmark.goal_id == None,
    ).all()
    unalloc_total = sum(
        e.percentage / 100.0 * (holding_map[e.holding_id].current_value if e.holding_id in holding_map else 0)
        for e in unalloc_earmarks
    )
    unalloc_schemas = [
        EarmarkSchema(
            id=e.id,
            holding_id=e.holding_id,
            goal_id=None,
            percentage=e.percentage,
            amount=e.percentage / 100.0 * (holding_map[e.holding_id].current_value if e.holding_id in holding_map else 0),
            locked_by_user=e.locked_by_user,
            reasoning_object_id=e.reasoning_object_id or "",
        )
        for e in unalloc_earmarks
    ]
    unallocated = UnallocatedBucket(total_value=round(unalloc_total, 2), earmarks=unalloc_schemas)

    # ── ReasoningObjects for dashboard ─────────────────────────────────────
    db_ros = db.query(DBReasoningObject).filter(DBReasoningObject.user_id == user_id).all()
    ro_schemas = [
        ROSchema(
            id=r.id,
            type=r.type,
            subject_ref=r.subject_ref,
            rule_id=r.rule_id,
            inputs_used=r.inputs_used,
            assumptions_referenced=r.assumptions_referenced,
            plain_language=r.plain_language,
        )
        for r in db_ros
    ]

    # ── Data freshness ─────────────────────────────────────────────────────
    user = db.get(User, user_id)
    latest_nav = db.query(NavCache).order_by(NavCache.nav_date.desc()).first()
    last_nav_date = latest_nav.nav_date if latest_nav else today

    nudge_due = False
    nudge_reason = None
    if user and user.last_cas_upload:
        from datetime import timedelta
        days_since = (datetime.now(timezone.utc) - user.last_cas_upload).days
        confirmed_sips = db.query(DBSip).filter(
            DBSip.user_id == user_id, DBSip.source == "confirmed"
        ).count()
        if confirmed_sips > 0 and days_since > 32:
            nudge_due = True
            nudge_reason = "Your SIPs should have posted — upload a fresh CAS to stay accurate."
        elif confirmed_sips == 0 and days_since > 90:
            nudge_due = True
            nudge_reason = "It has been over 90 days since your last upload. A fresh CAS will update your valuations."

    freshness = DataFreshness(
        last_cas_upload=user.last_cas_upload if user else None,
        last_nav_date=last_nav_date,
        nudge_due=nudge_due,
        nudge_reason=nudge_reason,
    )

    # ── Assemble and cache DashboardState ──────────────────────────────────
    now = datetime.now(timezone.utc)
    state = DashboardState(
        computed_at=now,
        portfolio_health=portfolio_health,
        goals=goal_views,
        unallocated=unallocated,
        reasoning_objects=ro_schemas,
        data_freshness=freshness,
    )

    # Cache it
    snapshot = db.get(DashboardSnapshot, user_id)
    state_json = state.model_dump(mode="json")
    if snapshot:
        snapshot.computed_at = now
        snapshot.state = state_json
    else:
        db.add(DashboardSnapshot(user_id=user_id, computed_at=now, state=state_json))

    db.commit()
    return state


def get_dashboard(user_id: str, db: Session) -> Optional[DashboardState]:
    """Return cached DashboardState without recomputing. None if never computed."""
    snapshot = db.get(DashboardSnapshot, user_id)
    if not snapshot:
        return None
    return DashboardState.model_validate(snapshot.state)
