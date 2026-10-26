"""migration merge

Revision ID: 1f2a446a01c0
Revises: 219eacbb330e, 342560179c01
Create Date: 2026-10-26 23:28:38.091134

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "1f2a446a01c0"
down_revision: str | Sequence[str] | None = ("219eacbb330e", "342560179c01")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""


def downgrade() -> None:
    """Downgrade schema."""
