from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth.deps import get_current_user
from app.models.db import User, Assumption as DBAssumption
from app.schemas.api import Assumption, AssumptionInput
from app.seeds.default_assumptions import seed_defaults

router = APIRouter(prefix="/assumptions", tags=["Assumptions"])


@router.get("", response_model=list[Assumption])
def get_assumptions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    seed_defaults(current_user.id, db)
    rows = db.query(DBAssumption).filter(DBAssumption.user_id == current_user.id).all()
    return [Assumption(key=r.key, value=r.value, is_default=r.is_default) for r in rows]


@router.patch("", response_model=list[Assumption])
def update_assumptions(
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    seed_defaults(current_user.id, db)
    inputs = body.get("assumptions", [])
    for item in inputs:
        inp = AssumptionInput(**item)
        row = db.query(DBAssumption).filter(
            DBAssumption.user_id == current_user.id,
            DBAssumption.key == inp.key,
        ).first()
        if row:
            row.value = inp.value
            row.is_default = False
        else:
            db.add(DBAssumption(
                user_id=current_user.id,
                key=inp.key,
                value=inp.value,
                is_default=False,
            ))
    db.commit()
    rows = db.query(DBAssumption).filter(DBAssumption.user_id == current_user.id).all()
    return [Assumption(key=r.key, value=r.value, is_default=r.is_default) for r in rows]
