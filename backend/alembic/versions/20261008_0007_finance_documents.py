"""Finance documents and reconciliation kinds.

Revision ID: 20261008_0007
Revises: 20261008_0006

- Document types `debt` and `recurring_payment` can be classified and approved.
- Reconciliation suggestions can be stored as `date_mismatch` and `amount_mismatch` near-misses.
- Debts and payment obligations record the processing job they were created from (`source_job_id`).
  A partial unique index allows at most one debt and one obligation per job, so re-approving a job cannot duplicate it.

Explicit SQL, downgrade refused.
"""

from alembic import op

revision = "20261008_0007"
down_revision = "20261008_0006"
branch_labels = None
depends_on = None

DOCUMENT_TYPES = (
    "purchase_bill", "purchase_order", "quotation", "sales_order", "sales_invoice", "goods_receipt", "receipt",
    "customer_document", "bank_statement", "salary", "emi", "interest", "debt", "recurring_payment", "other",
)
SUGGESTION_KINDS = ("matched", "ambiguous", "duplicate", "missing_in_ledger", "date_mismatch", "amount_mismatch")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE document_processing_jobs DROP CONSTRAINT ck_document_jobs_classification",
    f"ALTER TABLE document_processing_jobs ADD CONSTRAINT ck_document_jobs_classification "
    f"CHECK (classification IS NULL OR {_in('classification', DOCUMENT_TYPES)})",
    "ALTER TABLE reconciliation_suggestions DROP CONSTRAINT reconciliation_suggestions_kind_check",
    f"ALTER TABLE reconciliation_suggestions ADD CONSTRAINT reconciliation_suggestions_kind_check "
    f"CHECK ({_in('kind', SUGGESTION_KINDS)})",
    "ALTER TABLE debts ADD COLUMN source_job_id uuid REFERENCES document_processing_jobs(id) ON DELETE RESTRICT",
    "ALTER TABLE payment_obligations ADD COLUMN source_job_id uuid REFERENCES document_processing_jobs(id) ON DELETE RESTRICT",
    "CREATE UNIQUE INDEX uq_debts_source_job ON debts (source_job_id) WHERE source_job_id IS NOT NULL",
    "CREATE UNIQUE INDEX uq_obligations_source_job ON payment_obligations (source_job_id) WHERE source_job_id IS NOT NULL",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is refused. Restore from backup instead.")
