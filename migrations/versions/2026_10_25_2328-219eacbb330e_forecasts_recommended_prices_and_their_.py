"""forecasts, recommended prices and their queue

Revision ID: 219eacbb330e
Revises: 91ffe8cb8056
Create Date: 2026-10-25 23:28:04.270806

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "219eacbb330e"
down_revision: str | Sequence[str] | None = "91ffe8cb8056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "forecasts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("current_day_x", sa.Integer(), nullable=False),
        sa.Column("curves", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_forecasts_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_forecasts")),
    )
    with op.batch_alter_table("forecasts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_forecasts_service_id"), ["service_id"], unique=False
        )

    op.create_table(
        "pending_forecasts",
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_pending_forecasts_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("service_id", name=op.f("pk_pending_forecasts")),
    )
    op.create_table(
        "recommended_prices",
        sa.Column("forecast_id", sa.Integer(), nullable=False),
        sa.Column("od_id", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.CheckConstraint(
            "price >= 0", name=op.f("ck_recommended_prices_price_positive")
        ),
        sa.ForeignKeyConstraint(
            ["forecast_id"],
            ["forecasts.id"],
            name=op.f("fk_recommended_prices_forecast_id_forecasts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["od_id"],
            ["ods.id"],
            name=op.f("fk_recommended_prices_od_id_ods"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "forecast_id", "od_id", name=op.f("pk_recommended_prices")
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("recommended_prices")
    op.drop_table("pending_forecasts")
    with op.batch_alter_table("forecasts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_forecasts_service_id"))

    op.drop_table("forecasts")
