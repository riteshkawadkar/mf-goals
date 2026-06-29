"""CAS upload, holdings list, and SIP management endpoints."""
from __future__ import annotations
import os
import tempfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth.deps import get_current_user
from app.models.db import (
    User, Holding as DBHolding, TaxLot as DBTaxLot,
    ActiveSip as DBSip, NavCache,
)
from app.schemas.api import (
    CasUploadResponse, Holding as HoldingSchema, TaxLot as TaxLotSchema,
    DetectedSip, ActiveSip, ActiveSipInput,
)
from app.parse.cas_parser import parse_cas_file, ParsedHolding
from app.parse.sip_detector import detect_sips
from app.engine.characterize import classify_holding

router = APIRouter(tags=["Ingestion"])


@router.post("/cas/upload", response_model=CasUploadResponse)
async def upload_cas(
    file: UploadFile = File(...),
    password: str = Form(default=""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Write to temp file (parse-and-discard pattern)
    suffix = ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = parse_cas_file(tmp_path, password=password or None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Upsert holdings and lots
    holdings_imported = 0
    nav_cache_map: dict[str, float] = {}
    for row in db.query(NavCache).all():
        nav_cache_map[row.scheme_code] = row.nav

    for parsed in result.holdings:
        if not parsed.lots:
            continue

        # Find or create holding
        db_holding = db.query(DBHolding).filter(
            DBHolding.user_id == current_user.id,
            DBHolding.scheme_code == parsed.scheme_code,
        ).first()

        asset_class, equity_fraction, style_cluster_id, sector_tags = classify_holding(parsed.category)
        total_units = sum(lot.units for lot in parsed.lots)

        if db_holding:
            db_holding.scheme_name = parsed.scheme_name
            db_holding.amc = parsed.amc
            db_holding.category = parsed.category
            db_holding.asset_class = asset_class
            db_holding.equity_fraction = equity_fraction
            db_holding.style_cluster_id = style_cluster_id
            db_holding.sector_tags = sector_tags
            db_holding.current_units = total_units
            # Delete and re-insert tax lots (CAS is source of truth)
            db.query(DBTaxLot).filter(DBTaxLot.holding_id == db_holding.id).delete()
        else:
            db_holding = DBHolding(
                user_id=current_user.id,
                scheme_code=parsed.scheme_code,
                scheme_name=parsed.scheme_name,
                amc=parsed.amc,
                category=parsed.category,
                asset_class=asset_class,
                equity_fraction=equity_fraction,
                style_cluster_id=style_cluster_id,
                sector_tags=sector_tags,
                current_units=total_units,
            )
            db.add(db_holding)
            db.flush()

        for lot in parsed.lots:
            db.add(DBTaxLot(
                holding_id=db_holding.id,
                units=lot.units,
                nav_at_buy=lot.nav_at_buy,
                cost_basis=lot.cost_basis,
                buy_date=lot.buy_date,
                lock_until=lot.lock_until,
                gain_type=lot.gain_type,
            ))
        holdings_imported += 1

    current_user.last_cas_upload = datetime.now(timezone.utc)
    db.commit()

    # Detect SIPs
    lots_by_scheme: dict[str, list] = {}
    scheme_meta: dict[str, tuple] = {}
    for parsed in result.holdings:
        lots_by_scheme[parsed.scheme_code] = [
            (lot.buy_date, lot.cost_basis) for lot in parsed.lots
        ]
        scheme_meta[parsed.scheme_code] = (parsed.scheme_name, parsed.amc)

    detected_candidates = detect_sips(lots_by_scheme, scheme_meta)
    detected_sips = [
        DetectedSip(
            scheme_code=c.scheme_code,
            scheme_name=c.scheme_name,
            suggested_amount=c.suggested_amount,
            cadence=c.cadence,
            last_installment_date=c.last_installment_date,
            detection_confidence=c.detection_confidence,
        )
        for c in detected_candidates
    ]

    # Build response holdings with live NAV
    response_holdings = _build_holding_schemas(current_user.id, db, nav_cache_map)

    return CasUploadResponse(
        holdings_imported=holdings_imported,
        tax_lots_imported=result.total_tax_lots,
        holdings=response_holdings,
        detected_sips=detected_sips,
    )


@router.get("/holdings", response_model=list[HoldingSchema])
def list_holdings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    nav_cache_map = {row.scheme_code: row.nav for row in db.query(NavCache).all()}
    return _build_holding_schemas(current_user.id, db, nav_cache_map)


@router.get("/sips", response_model=list[ActiveSip])
def list_sips(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.query(DBSip).filter(
        DBSip.user_id == current_user.id,
        DBSip.source == "confirmed",
    ).all()
    return [
        ActiveSip(
            id=r.id,
            scheme_code=r.scheme_code,
            amount=r.amount,
            cadence=r.cadence,
            run_until=r.run_until,
            source=r.source,
        )
        for r in rows
    ]


@router.post("/sips/confirm", response_model=list[ActiveSip])
def confirm_sips(
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sips_input = body.get("sips", [])

    # Validate input
    validated: list[ActiveSipInput] = []
    for s in sips_input:
        try:
            validated.append(ActiveSipInput(**s))
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    # Replace all confirmed SIPs
    db.query(DBSip).filter(
        DBSip.user_id == current_user.id,
        DBSip.source == "confirmed",
    ).delete(synchronize_session=False)

    new_sips = []
    for s in validated:
        obj = DBSip(
            user_id=current_user.id,
            scheme_code=s.scheme_code,
            amount=s.amount,
            cadence=s.cadence,
            run_until=s.run_until,
            source="confirmed",
        )
        db.add(obj)
        new_sips.append(obj)

    db.commit()
    return [
        ActiveSip(
            id=s.id,
            scheme_code=s.scheme_code,
            amount=s.amount,
            cadence=s.cadence,
            run_until=s.run_until,
            source=s.source,
        )
        for s in new_sips
    ]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _build_holding_schemas(
    user_id: str,
    db: Session,
    nav_cache_map: dict[str, float],
) -> list[HoldingSchema]:
    db_holdings = db.query(DBHolding).filter(DBHolding.user_id == user_id).all()
    result = []
    for h in db_holdings:
        nav = nav_cache_map.get(h.scheme_code, 0.0)
        current_value = h.current_units * nav
        lots = [
            TaxLotSchema(
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
        result.append(HoldingSchema(
            id=h.id,
            scheme_code=h.scheme_code,
            scheme_name=h.scheme_name,
            amc=h.amc,
            category=h.category,
            asset_class=h.asset_class,
            equity_fraction=h.equity_fraction,
            style_cluster_id=h.style_cluster_id,
            sector_tags=h.sector_tags or [],
            current_units=h.current_units,
            current_nav=nav,
            current_value=current_value,
            tax_lots=lots,
        ))
    return result
