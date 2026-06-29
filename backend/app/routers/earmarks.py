"""PATCH /earmarks/{earmarkId} — user locks or adjusts an earmark.

After adjustment, re-reconcile so each holding sums to 100%.
Returns the full DashboardState (same as engine/run).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth.deps import get_current_user
from app.models.db import User, Earmark as DBEarmark, Holding as DBHolding, NavCache
from app.schemas.api import DashboardState
from app.engine.runner import run_engine

router = APIRouter(prefix="/earmarks", tags=["Earmarks"])


@router.patch("/{earmark_id}", response_model=DashboardState)
def patch_earmark(
    earmark_id: str,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    earmark = db.query(DBEarmark).filter(
        DBEarmark.id == earmark_id,
        DBEarmark.user_id == current_user.id,
    ).first()
    if not earmark:
        raise HTTPException(status_code=404, detail="Earmark not found")

    if "locked" in body:
        earmark.locked_by_user = bool(body["locked"])

    if "percentage" in body:
        new_pct = float(body["percentage"])
        if not (0 <= new_pct <= 100):
            raise HTTPException(status_code=422, detail="percentage must be 0-100")

        old_pct = earmark.percentage
        earmark.percentage = new_pct

        # Re-reconcile: adjust the Unallocated earmark for this holding
        # so the total stays at 100%
        _reconcile_holding(earmark.holding_id, earmark_id, old_pct, new_pct, current_user.id, db)

    db.commit()

    # Re-run engine to get fresh DashboardState
    return run_engine(current_user.id, db)


def _reconcile_holding(
    holding_id: str,
    changed_earmark_id: str,
    old_pct: float,
    new_pct: float,
    user_id: str,
    db: Session,
) -> None:
    """Adjust unallocated earmark to absorb the change in percentage."""
    delta = new_pct - old_pct  # positive = we took more, reduce unallocated

    unalloc = db.query(DBEarmark).filter(
        DBEarmark.holding_id == holding_id,
        DBEarmark.user_id == user_id,
        DBEarmark.goal_id == None,
        DBEarmark.id != changed_earmark_id,
    ).first()

    if unalloc:
        new_unalloc = unalloc.percentage - delta
        if new_unalloc < 0:
            # Insufficient unallocated; clamp and redistribute from other unlocked earmarks
            deficit = -new_unalloc
            new_unalloc = 0.0
            _reduce_other_earmarks(holding_id, changed_earmark_id, deficit, user_id, db)
        unalloc.percentage = new_unalloc
    else:
        # No unallocated row — reduce other unlocked earmarks
        _reduce_other_earmarks(holding_id, changed_earmark_id, delta, user_id, db)


def _reduce_other_earmarks(
    holding_id: str,
    skip_id: str,
    amount_pct: float,
    user_id: str,
    db: Session,
) -> None:
    others = db.query(DBEarmark).filter(
        DBEarmark.holding_id == holding_id,
        DBEarmark.user_id == user_id,
        DBEarmark.id != skip_id,
        DBEarmark.locked_by_user == False,
    ).all()
    total_other = sum(e.percentage for e in others)
    if total_other <= 0:
        return
    for e in others:
        fraction = e.percentage / total_other
        e.percentage = max(0.0, e.percentage - amount_pct * fraction)
