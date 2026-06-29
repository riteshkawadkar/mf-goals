import uuid
from datetime import datetime, date
from typing import Optional
from sqlalchemy import (
    String, Float, Boolean, Date, DateTime, Integer, ForeignKey,
    ARRAY, Text, UniqueConstraint, func
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    otp_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    otp_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    google_sub: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_cas_upload: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    holdings: Mapped[list["Holding"]] = relationship("Holding", back_populates="user", cascade="all, delete-orphan")
    goals: Mapped[list["Goal"]] = relationship("Goal", back_populates="user", cascade="all, delete-orphan")
    active_sips: Mapped[list["ActiveSip"]] = relationship("ActiveSip", back_populates="user", cascade="all, delete-orphan")
    assumptions: Mapped[list["Assumption"]] = relationship("Assumption", back_populates="user", cascade="all, delete-orphan")
    reasoning_objects: Mapped[list["ReasoningObject"]] = relationship("ReasoningObject", back_populates="user", cascade="all, delete-orphan")
    dashboard_snapshot: Mapped[Optional["DashboardSnapshot"]] = relationship("DashboardSnapshot", back_populates="user", uselist=False, cascade="all, delete-orphan")


class Holding(Base):
    __tablename__ = "holdings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    scheme_code: Mapped[str] = mapped_column(String, nullable=False)
    scheme_name: Mapped[str] = mapped_column(String, nullable=False)
    amc: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    asset_class: Mapped[str] = mapped_column(String, nullable=False)  # liquid/debt/hybrid/equity
    equity_fraction: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    style_cluster_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    sector_tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    current_units: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    __table_args__ = (UniqueConstraint("user_id", "scheme_code", name="uq_holding_user_scheme"),)

    user: Mapped["User"] = relationship("User", back_populates="holdings")
    tax_lots: Mapped[list["TaxLot"]] = relationship("TaxLot", back_populates="holding", cascade="all, delete-orphan")
    earmarks: Mapped[list["Earmark"]] = relationship("Earmark", back_populates="holding", cascade="all, delete-orphan")


class TaxLot(Base):
    __tablename__ = "tax_lots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    holding_id: Mapped[str] = mapped_column(String, ForeignKey("holdings.id", ondelete="CASCADE"), nullable=False, index=True)
    units: Mapped[float] = mapped_column(Float, nullable=False)
    nav_at_buy: Mapped[float] = mapped_column(Float, nullable=False)
    cost_basis: Mapped[float] = mapped_column(Float, nullable=False)
    buy_date: Mapped[date] = mapped_column(Date, nullable=False)
    lock_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    gain_type: Mapped[str] = mapped_column(String, nullable=False)  # stcg/ltcg/locked

    holding: Mapped["Holding"] = relationship("Holding", back_populates="tax_lots")


class ActiveSip(Base):
    __tablename__ = "active_sips"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    scheme_code: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    cadence: Mapped[str] = mapped_column(String, nullable=False)  # monthly/quarterly
    run_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False, default="detected")  # detected/confirmed

    user: Mapped["User"] = relationship("User", back_populates="active_sips")


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    archetype: Mapped[str] = mapped_column(String, nullable=False)
    target_today: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    horizon_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=99)
    inflation_rate: Mapped[float] = mapped_column(Float, nullable=False)
    target_future_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence_tag: Mapped[str] = mapped_column(String, nullable=False, default="medium")
    equity_band_low: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    equity_band_high: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    glide_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_perpetual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    user: Mapped["User"] = relationship("User", back_populates="goals")
    earmarks: Mapped[list["Earmark"]] = relationship("Earmark", back_populates="goal", cascade="all, delete-orphan")
    diagnosis: Mapped[Optional["Diagnosis"]] = relationship("Diagnosis", back_populates="goal", uselist=False, cascade="all, delete-orphan")


class Earmark(Base):
    __tablename__ = "earmarks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    holding_id: Mapped[str] = mapped_column(String, ForeignKey("holdings.id", ondelete="CASCADE"), nullable=False, index=True)
    goal_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("goals.id", ondelete="CASCADE"), nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    percentage: Mapped[float] = mapped_column(Float, nullable=False)
    locked_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reasoning_object_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("reasoning_objects.id"), nullable=True)

    holding: Mapped["Holding"] = relationship("Holding", back_populates="earmarks")
    goal: Mapped[Optional["Goal"]] = relationship("Goal", back_populates="earmarks")


class Assumption(Base):
    __tablename__ = "assumptions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_assumption_user_key"),)

    user: Mapped["User"] = relationship("User", back_populates="assumptions")


class NavCache(Base):
    __tablename__ = "nav_cache"

    scheme_code: Mapped[str] = mapped_column(String, primary_key=True)
    nav: Mapped[float] = mapped_column(Float, nullable=False)
    nav_date: Mapped[date] = mapped_column(Date, nullable=False)


class ReasoningObject(Base):
    __tablename__ = "reasoning_objects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String, nullable=False)  # earmark/flag/sufficiency/path_safety/portfolio_health
    subject_ref: Mapped[str] = mapped_column(String, nullable=False)
    rule_id: Mapped[str] = mapped_column(String, nullable=False)
    inputs_used: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    assumptions_referenced: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    plain_language: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="reasoning_objects")


class Diagnosis(Base):
    __tablename__ = "diagnoses"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_uuid)
    goal_id: Mapped[str] = mapped_column(String, ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    p10: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    p50: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    p90: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sufficiency_verdict: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    judged_against: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    path_safety_fragility: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    structural_flags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    stress_results: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    sufficiency_reasoning_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("reasoning_objects.id"), nullable=True)
    path_safety_reasoning_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("reasoning_objects.id"), nullable=True)

    goal: Mapped["Goal"] = relationship("Goal", back_populates="diagnosis")


class DashboardSnapshot(Base):
    __tablename__ = "dashboard_snapshots"

    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[dict] = mapped_column(JSONB, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="dashboard_snapshot")
