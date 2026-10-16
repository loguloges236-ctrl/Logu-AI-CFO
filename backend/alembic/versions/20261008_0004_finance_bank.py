"""Finance, read-only bank, reconciliation and notifications.

Revision ID: 20261008_0004
Revises: 20261008_0003

Every statement is explicit SQL, with no bulk table-creation helpers and no destructive DDL. Downgrade is refused.
Literal enum lists must stay equal to app/finance/domain.py, app/bank, app/notifications/domain.py and
app/commerce/extraction.py; tests/unit/test_finance_static.py checks that.

Database rules:
- payment_records is append-only. A reversal is a new negative row that references the original.
- A period's net payments can never be negative or exceed the obligation's amount (checked under a row lock).
- Debt schedule rows must split: emi = interest + principal, and the balance must roll forward.
- bank_transactions is append-only: imported bank rows are never edited or deleted.
- reconciliation_suggestions may change only their decision; the suggestion itself is immutable.
- notifications may change only read_at; they cannot be deleted. dedupe_key is unique.
"""

from alembic import op

revision = "20261008_0004"
down_revision = "20261008_0003"
branch_labels = None
depends_on = None

DOCUMENT_TYPES = (
    "purchase_bill", "purchase_order", "quotation", "sales_order", "sales_invoice", "goods_receipt", "receipt",
    "customer_document", "bank_statement", "salary", "emi", "interest", "other",
)
OBLIGATION_KINDS = (
    "rent", "emi", "insurance", "subscription", "supplier_settlement", "credit_card",
    "utilities", "salary", "interest", "other",
)
FREQUENCIES = ("once", "weekly", "monthly", "yearly")
INTEREST_TYPES = ("reducing", "flat")
NOTIFICATION_KINDS = (
    "emi_due", "debt_payment_due", "supplier_payment_due", "customer_payment_overdue", "salary_date",
    "recurring_expense", "document_review_required", "reconciliation_issue", "financial_risk_alert",
)
SUGGESTION_KINDS = ("matched", "ambiguous", "duplicate", "missing_in_ledger")
SUGGESTION_STATUSES = ("suggested", "confirmed", "rejected")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _ts() -> str:
    return "created_at timestamptz NOT NULL DEFAULT now(),\n    updated_at timestamptz NOT NULL DEFAULT now()"


STATEMENTS: tuple[str, ...] = (
    # ------------------------------------------------------------ widen document classification (Phase 3 list)
    "ALTER TABLE document_processing_jobs DROP CONSTRAINT ck_document_jobs_classification",
    f"ALTER TABLE document_processing_jobs ADD CONSTRAINT ck_document_jobs_classification "
    f"CHECK (classification IS NULL OR {_in('classification', DOCUMENT_TYPES)})",
    # ------------------------------------------------------------ debts
    f"""
    CREATE TABLE debts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        name varchar(120) NOT NULL,
        lender varchar(120) NOT NULL,
        principal numeric(18,2) NOT NULL CHECK (principal > 0),
        annual_rate numeric(7,4) NOT NULL CHECK (annual_rate >= 0 AND annual_rate <= 100),
        interest_type varchar(10) NOT NULL CHECK ({_in("interest_type", INTEREST_TYPES)}),
        emi numeric(18,2) NOT NULL CHECK (emi > 0),
        tenure_months integer NOT NULL CHECK (tenure_months BETWEEN 1 AND 600),
        start_date date NOT NULL,
        first_due_date date NOT NULL,
        end_date date NOT NULL,
        status varchar(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
        loan_account_id uuid REFERENCES accounts(id) ON DELETE RESTRICT,
        notes varchar(500),
        {_ts()},
        CONSTRAINT ck_debts_dates CHECK (end_date >= first_due_date AND first_due_date >= start_date)
    )
    """,
    """
    CREATE TABLE debt_schedule_items (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        debt_id uuid NOT NULL REFERENCES debts(id) ON DELETE RESTRICT,
        installment_no integer NOT NULL CHECK (installment_no > 0),
        due_date date NOT NULL,
        opening_balance numeric(18,2) NOT NULL CHECK (opening_balance >= 0),
        interest_amount numeric(18,2) NOT NULL CHECK (interest_amount >= 0),
        principal_amount numeric(18,2) NOT NULL CHECK (principal_amount >= 0),
        emi_amount numeric(18,2) NOT NULL CHECK (emi_amount > 0),
        closing_balance numeric(18,2) NOT NULL CHECK (closing_balance >= 0),
        CONSTRAINT uq_debt_schedule_installment UNIQUE (debt_id, installment_no),
        CONSTRAINT ck_debt_schedule_split CHECK (emi_amount = interest_amount + principal_amount),
        CONSTRAINT ck_debt_schedule_roll CHECK (opening_balance - principal_amount = closing_balance)
    )
    """,
    "CREATE INDEX ix_debt_schedule_due ON debt_schedule_items (due_date)",
    # ------------------------------------------------------------ obligations (recurring and one-off)
    f"""
    CREATE TABLE payment_obligations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        kind varchar(30) NOT NULL CHECK ({_in("kind", OBLIGATION_KINDS)}),
        title varchar(160) NOT NULL,
        counterparty varchar(160),
        amount numeric(18,2) NOT NULL CHECK (amount > 0),
        rate numeric(7,4) CHECK (rate IS NULL OR (rate >= 0 AND rate <= 100)),
        currency char(3) NOT NULL DEFAULT 'INR',
        frequency varchar(10) NOT NULL CHECK ({_in("frequency", FREQUENCIES)}),
        first_due_date date NOT NULL,
        end_date date,
        is_critical boolean NOT NULL DEFAULT false,
        status varchar(10) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'cancelled')),
        debt_id uuid REFERENCES debts(id) ON DELETE RESTRICT,
        expense_account_id uuid REFERENCES accounts(id) ON DELETE RESTRICT,
        {_ts()},
        CONSTRAINT ck_obligation_end CHECK (end_date IS NULL OR end_date >= first_due_date)
    )
    """,
    "CREATE INDEX ix_obligations_kind_due ON payment_obligations (kind, first_due_date)",
    "CREATE INDEX ix_obligations_debt ON payment_obligations (debt_id)",
    # ------------------------------------------------------------ payment records (append-only, signed)
    """
    CREATE TABLE payment_records (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        obligation_id uuid NOT NULL REFERENCES payment_obligations(id) ON DELETE RESTRICT,
        period_due_date date NOT NULL,
        amount numeric(18,2) NOT NULL CHECK (amount <> 0),
        paid_on date NOT NULL,
        reference varchar(80),
        journal_entry_id uuid REFERENCES journal_entries(id) ON DELETE RESTRICT,
        reverses_record_id uuid REFERENCES payment_records(id) ON DELETE RESTRICT,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_payment_reversal CHECK (reverses_record_id IS NULL OR amount < 0)
    )
    """,
    "CREATE INDEX ix_payment_records_period ON payment_records (obligation_id, period_due_date)",
    """
    CREATE OR REPLACE FUNCTION logu_finance_reject_change() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION '% on % is not allowed: this history is append-only', TG_OP, TG_TABLE_NAME;
    END
    $$
    """,
    """
    CREATE TRIGGER payment_records_append_only
        BEFORE UPDATE OR DELETE ON payment_records
        FOR EACH ROW EXECUTE FUNCTION logu_finance_reject_change()
    """,
    """
    CREATE TRIGGER payment_records_no_truncate
        BEFORE TRUNCATE ON payment_records
        FOR EACH STATEMENT EXECUTE FUNCTION logu_finance_reject_change()
    """,
    """
    CREATE OR REPLACE FUNCTION logu_finance_payment_cap() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        due_amount numeric;
        obligation_state text;
        period_net numeric;
    BEGIN
        SELECT amount, status INTO due_amount, obligation_state
        FROM payment_obligations WHERE id = NEW.obligation_id FOR UPDATE;
        IF obligation_state IS DISTINCT FROM 'open' THEN
            RAISE EXCEPTION 'payments cannot be recorded against an obligation that is not open';
        END IF;
        SELECT COALESCE(SUM(amount), 0) INTO period_net
        FROM payment_records
        WHERE obligation_id = NEW.obligation_id AND period_due_date = NEW.period_due_date;
        period_net := period_net + NEW.amount;
        IF period_net > due_amount THEN
            RAISE EXCEPTION 'payments for this period (%) would exceed the amount due (%)', period_net, due_amount;
        END IF;
        IF period_net < 0 THEN
            RAISE EXCEPTION 'a reversal cannot exceed the payments made for this period';
        END IF;
        RETURN NEW;
    END
    $$
    """,
    """
    CREATE TRIGGER payment_records_cap
        BEFORE INSERT ON payment_records
        FOR EACH ROW EXECUTE FUNCTION logu_finance_payment_cap()
    """,
    # ------------------------------------------------------------ bank (read-only data)
    f"""
    CREATE TABLE bank_accounts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        provider varchar(40) NOT NULL,
        external_ref varchar(120) NOT NULL,
        display_name varchar(120) NOT NULL,
        currency char(3) NOT NULL DEFAULT 'INR',
        ledger_account_id uuid REFERENCES accounts(id) ON DELETE RESTRICT,
        provider_balance numeric(18,2),
        balance_as_of date,
        is_active boolean NOT NULL DEFAULT true,
        {_ts()},
        CONSTRAINT uq_bank_accounts_ref UNIQUE (provider, external_ref)
    )
    """,
    """
    CREATE TABLE bank_transactions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        bank_account_id uuid NOT NULL REFERENCES bank_accounts(id) ON DELETE RESTRICT,
        external_id varchar(120) NOT NULL,
        txn_date date NOT NULL,
        amount numeric(18,2) NOT NULL CHECK (amount <> 0),
        description varchar(255) NOT NULL DEFAULT '',
        balance_after numeric(18,2),
        import_key varchar(64) NOT NULL,
        imported_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_bank_transactions_external UNIQUE (bank_account_id, external_id)
    )
    """,
    "CREATE INDEX ix_bank_transactions_date ON bank_transactions (bank_account_id, txn_date)",
    """
    CREATE TRIGGER bank_transactions_append_only
        BEFORE UPDATE OR DELETE ON bank_transactions
        FOR EACH ROW EXECUTE FUNCTION logu_finance_reject_change()
    """,
    """
    CREATE TRIGGER bank_transactions_no_truncate
        BEFORE TRUNCATE ON bank_transactions
        FOR EACH STATEMENT EXECUTE FUNCTION logu_finance_reject_change()
    """,
    # ------------------------------------------------------------ reconciliation suggestions
    f"""
    CREATE TABLE reconciliation_suggestions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        bank_transaction_id uuid NOT NULL REFERENCES bank_transactions(id) ON DELETE RESTRICT,
        journal_entry_id uuid REFERENCES journal_entries(id) ON DELETE RESTRICT,
        kind varchar(20) NOT NULL CHECK ({_in("kind", SUGGESTION_KINDS)}),
        score integer NOT NULL CHECK (score BETWEEN 0 AND 100),
        status varchar(12) NOT NULL DEFAULT 'suggested' CHECK ({_in("status", SUGGESTION_STATUSES)}),
        decided_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_recon_matched_has_entry CHECK (kind NOT IN ('matched', 'ambiguous') OR journal_entry_id IS NOT NULL)
    )
    """,
    "CREATE UNIQUE INDEX uq_recon_confirmed_bank ON reconciliation_suggestions (bank_transaction_id) WHERE status = 'confirmed'",
    "CREATE UNIQUE INDEX uq_recon_confirmed_entry ON reconciliation_suggestions (journal_entry_id) WHERE status = 'confirmed'",
    """
    CREATE OR REPLACE FUNCTION logu_guard_suggestion() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'reconciliation suggestions cannot be deleted';
        END IF;
        IF NEW.bank_transaction_id IS DISTINCT FROM OLD.bank_transaction_id
           OR NEW.journal_entry_id IS DISTINCT FROM OLD.journal_entry_id
           OR NEW.kind IS DISTINCT FROM OLD.kind
           OR NEW.score IS DISTINCT FROM OLD.score
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'only the decision of a reconciliation suggestion can change';
        END IF;
        RETURN NEW;
    END
    $$
    """,
    """
    CREATE TRIGGER reconciliation_suggestions_guard
        BEFORE UPDATE OR DELETE ON reconciliation_suggestions
        FOR EACH ROW EXECUTE FUNCTION logu_guard_suggestion()
    """,
    # ------------------------------------------------------------ notifications
    f"""
    CREATE TABLE notifications (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        kind varchar(40) NOT NULL CHECK ({_in("kind", NOTIFICATION_KINDS)}),
        title varchar(160) NOT NULL,
        body varchar(500) NOT NULL,
        dedupe_key varchar(200) NOT NULL,
        due_date date,
        channel varchar(30) NOT NULL DEFAULT 'in_app',
        read_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_notifications_dedupe UNIQUE (dedupe_key)
    )
    """,
    """
    CREATE OR REPLACE FUNCTION logu_guard_notification() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'notifications cannot be deleted';
        END IF;
        IF NEW.kind IS DISTINCT FROM OLD.kind OR NEW.title IS DISTINCT FROM OLD.title
           OR NEW.body IS DISTINCT FROM OLD.body OR NEW.dedupe_key IS DISTINCT FROM OLD.dedupe_key THEN
            RAISE EXCEPTION 'only the read state of a notification can change';
        END IF;
        RETURN NEW;
    END
    $$
    """,
    """
    CREATE TRIGGER notifications_guard
        BEFORE UPDATE OR DELETE ON notifications
        FOR EACH ROW EXECUTE FUNCTION logu_guard_notification()
    """,
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is refused. Restore from backup instead.")
