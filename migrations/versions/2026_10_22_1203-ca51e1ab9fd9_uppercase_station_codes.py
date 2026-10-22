"""uppercase station codes

Revision ID: ca51e1ab9fd9
Revises: d10f1e40d02c
Create Date: 2026-10-22 12:03:49.322585

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ca51e1ab9fd9"
down_revision: str | Sequence[str] | None = "d10f1e40d02c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Fails on the unique constraint if two stops differ only by case
    # (e.g. `ply` and `PLY`): they must be merged by hand first.
    op.execute("UPDATE stops SET code = UPPER(code)")
    op.execute(
        "UPDATE bookings SET origin = UPPER(origin), destination = UPPER(destination)"
    )
    with op.batch_alter_table("stops") as batch_op:
        batch_op.create_check_constraint("code_uppercase", "code = upper(code)")


def downgrade() -> None:
    """Downgrade schema."""
    # The data stays uppercase: the original case is not recorded.
    with op.batch_alter_table("stops") as batch_op:
        batch_op.drop_constraint(op.f("ck_stops_code_uppercase"), type_="check")
