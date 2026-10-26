"""price history, forecast points, compute requests

Revision ID: 00c8ffe39239
Revises: 219eacbb330e
Create Date: 2026-10-26 22:55:32.465908

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "00c8ffe39239"
down_revision: str | Sequence[str] | None = "219eacbb330e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "compute_requests",
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
            name=op.f("fk_compute_requests_service_id_services"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("service_id", name=op.f("pk_compute_requests")),
    )
    op.create_table(
        "forecast_points",
        sa.Column("forecast_id", sa.Integer(), nullable=False),
        sa.Column("od_id", sa.Integer(), nullable=False),
        sa.Column("day_x", sa.Integer(), nullable=False),
        sa.Column("bookings", sa.Double(), nullable=False),
        sa.ForeignKeyConstraint(
            ["forecast_id"],
            ["forecasts.id"],
            name=op.f("fk_forecast_points_forecast_id_forecasts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["od_id"],
            ["ods.id"],
            name=op.f("fk_forecast_points_od_id_ods"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "forecast_id", "od_id", "day_x", name=op.f("pk_forecast_points")
        ),
    )
    op.create_table(
        "price_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("od_id", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("previous_price", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("forecast_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "source IN ('import', 'analyst')",
            name=op.f("ck_price_changes_source_known"),
        ),
        sa.CheckConstraint("price >= 0", name=op.f("ck_price_changes_price_positive")),
        sa.ForeignKeyConstraint(
            ["forecast_id"],
            ["forecasts.id"],
            name=op.f("fk_price_changes_forecast_id_forecasts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["od_id"],
            ["ods.id"],
            name=op.f("fk_price_changes_od_id_ods"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_price_changes")),
    )
    with op.batch_alter_table("price_changes", schema=None) as batch_op:
        batch_op.create_index(
            "ix_price_changes_od_id_changed_at", ["od_id", "changed_at"], unique=False
        )

    # Current prices become the first row of each OD's history.
    op.execute(
        "INSERT INTO price_changes (od_id, price, source) "
        "SELECT id, price, 'import' FROM ods WHERE price IS NOT NULL"
    )

    # The queue is replaced, not renamed: `request-all` refills it.
    op.drop_table("pending_forecasts")
    # Past runs lose their curves: start over (recommended prices cascade).
    op.execute("DELETE FROM forecasts")
    with op.batch_alter_table("forecasts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("optimized_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("bookings_as_of", sa.Integer(), nullable=False))
        batch_op.drop_column("curves")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM forecasts")
    with op.batch_alter_table("forecasts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "curves",
                postgresql.JSONB(astext_type=sa.Text()),
                autoincrement=False,
                nullable=False,
            )
        )
        batch_op.drop_column("bookings_as_of")
        batch_op.drop_column("optimized_at")

    op.create_table(
        "pending_forecasts",
        sa.Column("service_id", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column(
            "queued_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
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
    with op.batch_alter_table("price_changes", schema=None) as batch_op:
        batch_op.drop_index("ix_price_changes_od_id_changed_at")

    op.drop_table("price_changes")
    op.drop_table("forecast_points")
    op.drop_table("compute_requests")
