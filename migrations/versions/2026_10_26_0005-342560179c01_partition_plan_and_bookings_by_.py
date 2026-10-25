"""partition bookings by departure month

Revision ID: 342560179c01
Revises: 91ffe8cb8056
Create Date: 2026-10-26 00:05:33.000000

"""

from collections.abc import Sequence
from datetime import timedelta

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "342560179c01"
down_revision: str | Sequence[str] | None = "91ffe8cb8056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    "id, idempotency_key, service_id, od_id, price, currency, "
    "sale_datetime, cancel_datetime"
)
RESET_SEQUENCE = (
    "SELECT setval(pg_get_serial_sequence('bookings', 'id'), "
    "coalesce(max(id), 1), max(id) IS NOT NULL) FROM bookings"
)


def _checks_and_fks() -> list[sa.schema.SchemaItem]:
    return [
        sa.CheckConstraint(
            "cancel_datetime IS NULL OR cancel_datetime >= sale_datetime",
            name=op.f("ck_bookings_cancel_after_sale"),
        ),
        sa.CheckConstraint("price >= 0", name=op.f("ck_bookings_price_positive")),
        sa.ForeignKeyConstraint(
            ["od_id"], ["ods.id"], name=op.f("fk_bookings_od_id_ods")
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_bookings_service_id_services"),
        ),
    ]


def _columns() -> list[sa.schema.SchemaItem]:
    return [
        # Composite primary key: the sequence is not inferred.
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("od_id", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("sale_datetime", sa.DateTime(), nullable=False),
        sa.Column("cancel_datetime", sa.DateTime(), nullable=True),
    ]


def _indexes(*extra: str) -> None:
    op.create_index(op.f("ix_bookings_od_id"), "bookings", ["od_id"])
    op.create_index(op.f("ix_bookings_service_id"), "bookings", ["service_id"])
    op.create_index(
        "ix_bookings_natural_key",
        "bookings",
        ["od_id", "sale_datetime", "price", "currency", *extra],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NULL"),
    )


def upgrade() -> None:
    """Recreate bookings partitioned by month of the service's departure date.

    A table cannot be turned into a partitioned one: the rows are kept aside,
    the table recreated, one partition created per month present, rows copied.
    """
    op.execute(
        "CREATE TEMP TABLE old_bookings AS "
        "SELECT b.*, s.departure_date FROM bookings b "
        "JOIN services s ON s.id = b.service_id"
    )
    op.drop_table("bookings")
    op.create_table(
        "bookings",
        *_columns(),
        sa.Column("departure_date", sa.Date(), nullable=False),
        *_checks_and_fks(),
        sa.PrimaryKeyConstraint("id", "departure_date", name=op.f("pk_bookings")),
        sa.UniqueConstraint(
            "idempotency_key",
            "departure_date",
            name=op.f("uq_bookings_idempotency_key_departure_date"),
        ),
        postgresql_partition_by="RANGE (departure_date)",
    )
    _indexes("departure_date")

    months = op.get_bind().scalars(
        sa.text(
            "SELECT DISTINCT date_trunc('month', departure_date)::date "
            "FROM old_bookings"
        )
    )
    for first in months:
        nxt = (first + timedelta(days=31)).replace(day=1)
        op.execute(
            f"CREATE TABLE bookings_{first:%Y_%m} PARTITION OF bookings "
            f"FOR VALUES FROM ('{first}') TO ('{nxt}')"
        )
    op.execute(
        f"INSERT INTO bookings ({COLUMNS}, departure_date) "
        f"SELECT {COLUMNS}, departure_date FROM old_bookings"
    )
    op.execute(RESET_SEQUENCE)


def downgrade() -> None:
    """Recreate the flat bookings table. Fails if an idempotency key repeats."""
    op.execute("CREATE TEMP TABLE old_bookings AS SELECT * FROM bookings")
    op.drop_table("bookings")
    op.create_table(
        "bookings",
        *_columns(),
        *_checks_and_fks(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bookings")),
        sa.UniqueConstraint(
            "idempotency_key", name=op.f("uq_bookings_idempotency_key")
        ),
    )
    _indexes()
    op.execute(f"INSERT INTO bookings ({COLUMNS}) SELECT {COLUMNS} FROM old_bookings")
    op.execute(RESET_SEQUENCE)
