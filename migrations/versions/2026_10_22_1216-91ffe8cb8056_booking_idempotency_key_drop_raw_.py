"""booking idempotency key, drop raw payload columns

Revision ID: 91ffe8cb8056
Revises: ca51e1ab9fd9
Create Date: 2026-10-22 12:16:40.328213

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "91ffe8cb8056"
down_revision: str | Sequence[str] | None = "ca51e1ab9fd9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("bookings") as batch_op:
        batch_op.add_column(
            sa.Column("idempotency_key", sa.String(length=255), nullable=True)
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_bookings_idempotency_key"), ["idempotency_key"]
        )
        # Fails if keyless duplicates already exist: they must be removed first.
        batch_op.create_index(
            "ix_bookings_natural_key",
            ["od_id", "sale_datetime", "price", "currency"],
            unique=True,
            postgresql_where=sa.text("idempotency_key IS NULL"),
        )
        # Reachable through service_id / od_id.
        batch_op.drop_constraint(
            batch_op.f("ck_bookings_distinct_stops"), type_="check"
        )
        batch_op.drop_column("service_name")
        batch_op.drop_column("departure_date")
        batch_op.drop_column("origin")
        batch_op.drop_column("destination")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("bookings") as batch_op:
        batch_op.add_column(sa.Column("service_name", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("departure_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("origin", sa.String(16), nullable=True))
        batch_op.add_column(sa.Column("destination", sa.String(16), nullable=True))
    # Rebuild the dropped columns from the transport plan.
    op.execute(
        """
        UPDATE bookings b
        SET service_name = s.name,
            departure_date = s.departure_date,
            origin = o.code,
            destination = d.code
        FROM ods od
        JOIN services s ON s.id = od.service_id
        JOIN stops o ON o.id = od.origin_id
        JOIN stops d ON d.id = od.destination_id
        WHERE od.id = b.od_id
        """
    )
    with op.batch_alter_table("bookings") as batch_op:
        for column in ("service_name", "departure_date", "origin", "destination"):
            batch_op.alter_column(column, nullable=False)
        batch_op.create_check_constraint("distinct_stops", "origin <> destination")
        batch_op.drop_index(
            "ix_bookings_natural_key",
            postgresql_where=sa.text("idempotency_key IS NULL"),
        )
        batch_op.drop_constraint(
            batch_op.f("uq_bookings_idempotency_key"), type_="unique"
        )
        batch_op.drop_column("idempotency_key")
