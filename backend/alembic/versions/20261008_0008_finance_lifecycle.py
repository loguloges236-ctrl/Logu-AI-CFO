"""Settlement lifecycle requests, obligation payment idempotency and journal links.

Revision ID: 20261008_0008
Revises: 20261008_0007

- settlement_requests: a settlement is first requested (pending), then approved (creates the settlement and its
  journal through the service) or rejected. A decided request is final: the trigger allows only the one decision.
- payment_records: one journal entry per payment row, and one original payment per (obligation, reference), so a
  retried payment cannot post twice.

Explicit SQL, downgrade refused.
"""

from alembic import op

revision = "20261008_0008"
down_revision = "20261008_0007"
branch_labels = None
depends_on = None

REQUEST_STATUSES = ("pending", "approved", "rejected")
DIRECTIONS = ("receivable", "payable")
METHODS = ("cash", "bank", "upi", "cheque", "card", "other")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


STATEMENTS: tuple[str, ...] = (
    f"""
    CREATE TABLE settlement_requests (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        direction varchar(10) NOT NULL CHECK ({_in("direction", DIRECTIONS)}),
        sales_invoice_id uuid REFERENCES sales_invoices(id) ON DELETE RESTRICT,
        purchase_bill_id uuid REFERENCES purchase_bills(id) ON DELETE RESTRICT,
        amount numeric(18,2) NOT NULL CHECK (amount > 0),
        settled_on date NOT NULL,
        method varchar(12) NOT NULL CHECK ({_in("method", METHODS)}),
        reference varchar(80) NOT NULL CHECK (length(btrim(reference)) > 0),
        cash_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
        control_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
        status varchar(10) NOT NULL DEFAULT 'pending' CHECK ({_in("status", REQUEST_STATUSES)}),
        settlement_id uuid UNIQUE REFERENCES settlements(id) ON DELETE RESTRICT,
        decided_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_settlement_request_one_document CHECK ((sales_invoice_id IS NULL) <> (purchase_bill_id IS NULL)),
        CONSTRAINT ck_settlement_request_direction CHECK (
            (direction = 'receivable' AND sales_invoice_id IS NOT NULL)
            OR (direction = 'payable' AND purchase_bill_id IS NOT NULL)
        ),
        CONSTRAINT ck_settlement_request_accounts CHECK (cash_account_id <> control_account_id),
        CONSTRAINT ck_settlement_request_decision CHECK (
            (status = 'pending' AND decided_at IS NULL AND settlement_id IS NULL)
            OR (status = 'approved' AND decided_at IS NOT NULL AND settlement_id IS NOT NULL)
            OR (status = 'rejected' AND decided_at IS NOT NULL AND settlement_id IS NULL)
        )
    )
    """,
    "CREATE INDEX ix_settlement_requests_status ON settlement_requests (status, created_at)",
    """
    CREATE OR REPLACE FUNCTION logu_guard_settlement_request() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'settlement requests cannot be deleted';
        END IF;
        IF OLD.status <> 'pending' THEN
            RAISE EXCEPTION 'a decided settlement request is final';
        END IF;
        IF NEW.direction IS DISTINCT FROM OLD.direction
           OR NEW.sales_invoice_id IS DISTINCT FROM OLD.sales_invoice_id
           OR NEW.purchase_bill_id IS DISTINCT FROM OLD.purchase_bill_id
           OR NEW.amount IS DISTINCT FROM OLD.amount
           OR NEW.settled_on IS DISTINCT FROM OLD.settled_on
           OR NEW.method IS DISTINCT FROM OLD.method
           OR NEW.reference IS DISTINCT FROM OLD.reference
           OR NEW.cash_account_id IS DISTINCT FROM OLD.cash_account_id
           OR NEW.control_account_id IS DISTINCT FROM OLD.control_account_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'only the decision of a settlement request can change';
        END IF;
        RETURN NEW;
    END
    $$
    """,
    """
    CREATE TRIGGER settlement_requests_guard
        BEFORE UPDATE OR DELETE ON settlement_requests
        FOR EACH ROW EXECUTE FUNCTION logu_guard_settlement_request()
    """,
    "CREATE UNIQUE INDEX uq_payment_records_journal ON payment_records (journal_entry_id) WHERE journal_entry_id IS NOT NULL",
    "CREATE UNIQUE INDEX uq_payment_records_reference ON payment_records (obligation_id, reference) "
    "WHERE reference IS NOT NULL AND reverses_record_id IS NULL",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is refused. Restore from backup instead.")
