from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth.deps import get_current_user
from app.models.db import User
from app.schemas.api import DashboardState
from app.engine.runner import run_engine, get_dashboard

router = APIRouter(tags=["Engine"])


@router.post("/engine/run", response_model=DashboardState)
def engine_run(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return run_engine(current_user.id, db)


@router.get("/dashboard", response_model=DashboardState)
def dashboard(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    state = get_dashboard(current_user.id, db)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail="No computed state found. Call POST /engine/run first.",
        )
    return state
