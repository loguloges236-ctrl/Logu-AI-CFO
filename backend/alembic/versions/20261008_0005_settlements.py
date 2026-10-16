"""Settlements: receivable and payable tracking against posted sales invoices and purchase bills.

Revision ID: 20261008_0005
Revises: 20261008_0004

Every statement is explicit SQL. Downgrade is refused.

Database rules:
- settlements is append-only. A reversal is a new negative row that points at the positive row it reverses.
- Per document, the net settled amount is never negative and never above grand_total (checked under a row lock).
- Only posted documents can be settled.
- Exactly one document is referenced, and its direction must match (receivable -> sales invoice, payable -> bill).
- A journal entry for each settlement is linked by the application through the accounting service. The database
  cannot prove that the entry balances; the accounting engine does that.
"""

from alembic import op

revision = "20261008_0005"
down_revision = "20261008_0004"
branch_labels = None
depends_on = None

DIRECTIONS = ("receivable", "payable")
METHODS = ("cash", "bank", "upi", "cheque", "card", "other")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


STATEMENTS: tuple[str, ...] = (
    f"""
    CREATE TABLE settlements (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        direction varchar(10) NOT NULL CHECK ({_in("direction", DIRECTIONS)}),
        sales_invoice_id uuid REFERENCES sales_invoices(id) ON DELETE RESTRICT,
        purchase_bill_id uuid REFERENCES purchase_bills(id) ON DELETE RESTRICT,
        amount numeric(18,2) NOT NULL CHECK (amount <> 0),
        settled_on date NOT NULL,
        method varchar(12) NOT NULL CHECK ({_in("method", METHODS)}),
        reference varchar(80),
        journal_entry_id uuid REFERENCES journal_entries(id) ON DELETE RESTRICT,
        reverses_settlement_id uuid REFERENCES settlements(id) ON DELETE RESTRICT,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_settlement_one_document CHECK ((sales_invoice_id IS NULL) <> (purchase_bill_id IS NULL)),
        CONSTRAINT ck_settlement_direction CHECK (
            (direction = 'receivable' AND sales_invoice_id IS NOT NULL)
            OR (direction = 'payable' AND purchase_bill_id IS NOT NULL)
        ),
        CONSTRAINT ck_settlement_reversal CHECK (reverses_settlement_id IS NULL OR amount < 0)
    )
    """,
    "CREATE INDEX ix_settlements_invoice ON settlements (sales_invoice_id)",
    "CREATE INDEX ix_settlements_bill ON settlements (purchase_bill_id)",
    """
    CREATE OR REPLACE FUNCTION logu_settlement_reject_change() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION '% on settlements is not allowed: this history is append-only', TG_OP;
    END
    $$
    """,
    """
    CREATE TRIGGER settlements_append_only
        BEFORE UPDATE OR DELETE ON settlements
        FOR EACH ROW EXECUTE FUNCTION logu_settlement_reject_change()
    """,
    """
    CREATE TRIGGER settlements_no_truncate
        BEFORE TRUNCATE ON settlements
        FOR EACH STATEMENT EXECUTE FUNCTION logu_settlement_reject_change()
    """,
    """
    CREATE OR REPLACE FUNCTION logu_settlement_cap() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        doc_total numeric;
        doc_status text;
        net_settled numeric;
        original_amount numeric;
        original_sales uuid;
        original_bill uuid;
        original_reverses uuid;
        already_reversed numeric;
    BEGIN
        IF (NEW.direction = 'receivable' AND NEW.sales_invoice_id IS NULL)
           OR (NEW.direction = 'payable' AND NEW.purchase_bill_id IS NULL) THEN
            RAISE EXCEPTION 'settlement direction does not match the referenced document';
        END IF;
        -- Lock the parent document so two settlements cannot both pass the cap.
        IF NEW.direction = 'receivable' THEN
            SELECT grand_total, status INTO doc_total, doc_status
            FROM sales_invoices WHERE id = NEW.sales_invoice_id FOR UPDATE;
        ELSE
            SELECT grand_total, status INTO doc_total, doc_status
            FROM purchase_bills WHERE id = NEW.purchase_bill_id FOR UPDATE;
        END IF;
        IF doc_status IS DISTINCT FROM 'posted' THEN
            RAISE EXCEPTION 'only posted documents can be settled (status is %)', doc_status;
        END IF;

        IF NEW.reverses_settlement_id IS NOT NULL THEN
            SELECT amount, sales_invoice_id, purchase_bill_id, reverses_settlement_id
            INTO original_amount, original_sales, original_bill, original_reverses
            FROM settlements WHERE id = NEW.reverses_settlement_id;
            IF original_reverses IS NOT NULL OR original_amount <= 0 THEN
                RAISE EXCEPTION 'only a positive settlement can be reversed';
            END IF;
            IF original_sales IS DISTINCT FROM NEW.sales_invoice_id
               OR original_bill IS DISTINCT FROM NEW.purchase_bill_id THEN
                RAISE EXCEPTION 'a reversal must reference the same document';
            END IF;
            SELECT COALESCE(SUM(amount), 0) INTO already_reversed
            FROM settlements WHERE reverses_settlement_id = NEW.reverses_settlement_id;
            IF already_reversed + NEW.amount < -original_amount THEN
                RAISE EXCEPTION 'reversals cannot exceed the original settlement of %', original_amount;
            END IF;
        END IF;

        SELECT COALESCE(SUM(amount), 0) INTO net_settled
        FROM settlements
        WHERE direction = NEW.direction
          AND sales_invoice_id IS NOT DISTINCT FROM NEW.sales_invoice_id
          AND purchase_bill_id IS NOT DISTINCT FROM NEW.purchase_bill_id;
        net_settled := net_settled + NEW.amount;
        IF net_settled < 0 THEN
            RAISE EXCEPTION 'net settlement for this document cannot be negative';
        END IF;
        IF net_settled > doc_total THEN
            RAISE EXCEPTION 'settlements (%) would exceed the document total (%)', net_settled, doc_total;
        END IF;
        RETURN NEW;
    END
    $$
    """,
    """
    CREATE TRIGGER settlements_cap
        BEFORE INSERT ON settlements
        FOR EACH ROW EXECUTE FUNCTION logu_settlement_cap()
    """,
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is refused. Restore from backup instead.")
