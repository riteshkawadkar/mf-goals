from datetime import date
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth.deps import get_current_user
from app.models.db import User, Goal as DBGoal
from app.schemas.api import Goal as GoalSchema, GoalInput
from app.engine.characterize import characterize_goal
from app.seeds.default_assumptions import get_assumption_map

router = APIRouter(prefix="/goals", tags=["Goals"])


def _characterize_and_save(
    db_goal: DBGoal,
    inp: GoalInput,
    assumptions: dict,
    today: date,
) -> None:
    derived = characterize_goal(
        name=inp.name,
        archetype=inp.archetype,
        target_today=inp.target_today,
        horizon_date=inp.horizon_date,
        priority=inp.priority,
        inflation_rate=inp.inflation_rate,
        assumptions=assumptions,
        today=today,
    )
    db_goal.name = inp.name
    db_goal.archetype = inp.archetype
    db_goal.target_today = inp.target_today
    db_goal.horizon_date = inp.horizon_date
    db_goal.priority = inp.priority
    db_goal.inflation_rate = derived["inflation_rate"]
    db_goal.target_future_value = derived["target_future_value"]
    db_goal.confidence_tag = derived["confidence_tag"]
    db_goal.equity_band_low = derived["equity_band_low"]
    db_goal.equity_band_high = derived["equity_band_high"]
    db_goal.glide_start_date = derived["glide_start_date"]
    db_goal.is_perpetual = derived["is_perpetual"]


def _to_schema(g: DBGoal) -> GoalSchema:
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


@router.get("", response_model=list[GoalSchema])
def list_goals(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    goals = db.query(DBGoal).filter(DBGoal.user_id == current_user.id).order_by(DBGoal.priority).all()
    return [_to_schema(g) for g in goals]


@router.post("", response_model=GoalSchema, status_code=status.HTTP_201_CREATED)
def create_goal(
    inp: GoalInput,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assumptions = get_assumption_map(current_user.id, db)
    db_goal = DBGoal(user_id=current_user.id)
    _characterize_and_save(db_goal, inp, assumptions, date.today())
    db.add(db_goal)
    db.commit()
    db.refresh(db_goal)
    return _to_schema(db_goal)


@router.patch("/{goal_id}", response_model=GoalSchema)
def update_goal(
    goal_id: str,
    inp: GoalInput,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db_goal = db.query(DBGoal).filter(
        DBGoal.id == goal_id, DBGoal.user_id == current_user.id
    ).first()
    if not db_goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    assumptions = get_assumption_map(current_user.id, db)
    _characterize_and_save(db_goal, inp, assumptions, date.today())
    db.commit()
    db.refresh(db_goal)
    return _to_schema(db_goal)


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_goal(
    goal_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db_goal = db.query(DBGoal).filter(
        DBGoal.id == goal_id, DBGoal.user_id == current_user.id
    ).first()
    if not db_goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    db.delete(db_goal)
    db.commit()
