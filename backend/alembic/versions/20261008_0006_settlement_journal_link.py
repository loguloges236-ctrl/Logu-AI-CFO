"""One journal entry belongs to at most one settlement row.

Revision ID: 20261008_0006
Revises: 20261008_0005

Idempotency for settlement posting: a settlement cannot be posted twice because its journal_entry_id is set once,
and no two settlement rows can point at the same journal entry. Explicit SQL, downgrade refused.
"""

from alembic import op

revision = "20261008_0006"
down_revision = "20261008_0005"
branch_labels = None
depends_on = None

STATEMENTS: tuple[str, ...] = (
    "CREATE UNIQUE INDEX uq_settlements_journal ON settlements (journal_entry_id) WHERE journal_entry_id IS NOT NULL",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is refused. Restore from backup instead.")
