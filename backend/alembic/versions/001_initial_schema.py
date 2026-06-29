"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-06-29
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("otp_hash", sa.String(), nullable=True),
        sa.Column("otp_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("google_sub", sa.String(), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_cas_upload", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "holdings",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scheme_code", sa.String(), nullable=False),
        sa.Column("scheme_name", sa.String(), nullable=False),
        sa.Column("amc", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("asset_class", sa.String(), nullable=False),
        sa.Column("equity_fraction", sa.Float(), nullable=False, server_default="0"),
        sa.Column("style_cluster_id", sa.String(), nullable=False, server_default="''"),
        sa.Column("sector_tags", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("current_units", sa.Float(), nullable=False, server_default="0"),
        sa.UniqueConstraint("user_id", "scheme_code", name="uq_holding_user_scheme"),
    )
    op.create_index("ix_holdings_user_id", "holdings", ["user_id"])

    op.create_table(
        "tax_lots",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("holding_id", sa.String(), sa.ForeignKey("holdings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("units", sa.Float(), nullable=False),
        sa.Column("nav_at_buy", sa.Float(), nullable=False),
        sa.Column("cost_basis", sa.Float(), nullable=False),
        sa.Column("buy_date", sa.Date(), nullable=False),
        sa.Column("lock_until", sa.Date(), nullable=True),
        sa.Column("gain_type", sa.String(), nullable=False),
    )
    op.create_index("ix_tax_lots_holding_id", "tax_lots", ["holding_id"])

    op.create_table(
        "active_sips",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scheme_code", sa.String(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("cadence", sa.String(), nullable=False),
        sa.Column("run_until", sa.Date(), nullable=True),
        sa.Column("source", sa.String(), nullable=False, server_default="'detected'"),
    )
    op.create_index("ix_active_sips_user_id", "active_sips", ["user_id"])

    op.create_table(
        "goals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("archetype", sa.String(), nullable=False),
        sa.Column("target_today", sa.Float(), nullable=True),
        sa.Column("horizon_date", sa.Date(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="99"),
        sa.Column("inflation_rate", sa.Float(), nullable=False),
        sa.Column("target_future_value", sa.Float(), nullable=True),
        sa.Column("confidence_tag", sa.String(), nullable=False, server_default="'medium'"),
        sa.Column("equity_band_low", sa.Float(), nullable=False, server_default="0"),
        sa.Column("equity_band_high", sa.Float(), nullable=False, server_default="1"),
        sa.Column("glide_start_date", sa.Date(), nullable=True),
        sa.Column("is_perpetual", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_goals_user_id", "goals", ["user_id"])

    op.create_table(
        "reasoning_objects",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("subject_ref", sa.String(), nullable=False),
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("inputs_used", JSONB(), nullable=False, server_default="'{}'"),
        sa.Column("assumptions_referenced", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("plain_language", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_reasoning_objects_user_id", "reasoning_objects", ["user_id"])

    op.create_table(
        "earmarks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("holding_id", sa.String(), sa.ForeignKey("holdings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("goal_id", sa.String(), sa.ForeignKey("goals.id", ondelete="CASCADE"), nullable=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("percentage", sa.Float(), nullable=False),
        sa.Column("locked_by_user", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reasoning_object_id", sa.String(), sa.ForeignKey("reasoning_objects.id"), nullable=True),
    )
    op.create_index("ix_earmarks_holding_id", "earmarks", ["holding_id"])
    op.create_index("ix_earmarks_goal_id", "earmarks", ["goal_id"])
    op.create_index("ix_earmarks_user_id", "earmarks", ["user_id"])

    op.create_table(
        "assumptions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="true"),
        sa.UniqueConstraint("user_id", "key", name="uq_assumption_user_key"),
    )
    op.create_index("ix_assumptions_user_id", "assumptions", ["user_id"])

    op.create_table(
        "nav_cache",
        sa.Column("scheme_code", sa.String(), primary_key=True),
        sa.Column("nav", sa.Float(), nullable=False),
        sa.Column("nav_date", sa.Date(), nullable=False),
    )

    op.create_table(
        "diagnoses",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("goal_id", sa.String(), sa.ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("p10", sa.Float(), nullable=True),
        sa.Column("p50", sa.Float(), nullable=True),
        sa.Column("p90", sa.Float(), nullable=True),
        sa.Column("sufficiency_verdict", sa.String(), nullable=True),
        sa.Column("judged_against", sa.String(), nullable=True),
        sa.Column("path_safety_fragility", sa.String(), nullable=True),
        sa.Column("structural_flags", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("stress_results", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("sufficiency_reasoning_id", sa.String(), sa.ForeignKey("reasoning_objects.id"), nullable=True),
        sa.Column("path_safety_reasoning_id", sa.String(), sa.ForeignKey("reasoning_objects.id"), nullable=True),
    )
    op.create_index("ix_diagnoses_user_id", "diagnoses", ["user_id"])

    op.create_table(
        "dashboard_snapshots",
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", JSONB(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("dashboard_snapshots")
    op.drop_table("diagnoses")
    op.drop_table("nav_cache")
    op.drop_table("assumptions")
    op.drop_table("earmarks")
    op.drop_table("reasoning_objects")
    op.drop_table("goals")
    op.drop_table("active_sips")
    op.drop_table("tax_lots")
    op.drop_table("holdings")
    op.drop_table("users")
