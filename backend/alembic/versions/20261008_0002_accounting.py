"""Phase 2 core accounting schema (explicit DDL, PostgreSQL).

Revision ID: 20261008_0002
Revises: 20261007_0001
Create Date: 2026-10-08

Creates accounts, accounting_periods, journal_entries and journal_lines, plus the database
rules that protect accounting history:

- posted entries and their lines cannot be updated or deleted (reversal is the only correction);
- a posted entry must balance (checked at commit, so lines can be inserted first);
- posting needs an OPEN period that covers the posting date;
- account code/category cannot change once used, system accounts keep code and category,
  and accounts are never deleted;
- periods cannot be deleted, their dates cannot change, and a closed period cannot be reopened;
- periods cannot overlap (EXCLUDE constraint on daterange).

Literal enum lists below are part of the schema and must not be imported from application code.
"""

import sqlalchemy as sa
from alembic import op

revision = "20261008_0002"
down_revision = "20261007_0001"
branch_labels = None
depends_on = None

_CATEGORIES = ("asset", "liability", "equity", "income", "expense")
_SUBTYPES = (
    "bank",
    "cash",
    "capital",
    "header",
    "inventory",
    "general",
    "loan",
    "operating_expense",
    "other_financial",
    "payable",
    "petty_cash",
    "receivable",
    "sales",
)
_PERIOD_STATUSES = ("open", "closed")
_ENTRY_STATUSES = ("draft", "posted")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _ts() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(length=10), nullable=False, unique=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("subtype", sa.String(length=40), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("parent_id", sa.Uuid(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_ts(),
        sa.CheckConstraint("code ~ '^[0-9]{4,10}$'", name="ck_accounts_code_format"),
        sa.CheckConstraint(_in_list("category", _CATEGORIES), name="ck_accounts_category"),
        sa.CheckConstraint(_in_list("subtype", _SUBTYPES), name="ck_accounts_subtype"),
        sa.CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_accounts_parent_not_self"),
    )
    op.create_index("ix_accounts_category", "accounts", ["category"])
    op.create_index("ix_accounts_parent", "accounts", ["parent_id"])

    op.create_table(
        "accounting_periods",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False, unique=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False, server_default=sa.text("'open'")),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
        sa.CheckConstraint("start_date <= end_date", name="ck_periods_dates"),
        sa.CheckConstraint(_in_list("status", _PERIOD_STATUSES), name="ck_periods_status"),
        sa.CheckConstraint("(status = 'closed') = (closed_at IS NOT NULL)", name="ck_periods_closed_at"),
    )
    op.execute(
        """
        ALTER TABLE accounting_periods
        ADD CONSTRAINT ex_periods_no_overlap
        EXCLUDE USING gist (daterange(start_date, end_date, '[]') WITH &&)
        """
    )

    op.create_table(
        "journal_entries",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("entry_number", sa.Integer(), nullable=True, unique=True),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("posting_date", sa.Date(), nullable=True),
        sa.Column("reference_type", sa.String(length=40), nullable=True),
        sa.Column("reference_id", sa.String(length=120), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False, server_default=sa.text("'draft'")),
        sa.Column(
            "reverses_entry_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("journal_entries.id", ondelete="RESTRICT"),
            nullable=True,
            unique=True,
        ),
        sa.Column(
            "created_by_owner_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("owners.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        *_ts(),
        sa.CheckConstraint(_in_list("status", _ENTRY_STATUSES), name="ck_journal_entries_status"),
        sa.CheckConstraint(
            "status = 'draft' OR (posting_date IS NOT NULL AND entry_number IS NOT NULL)",
            name="ck_journal_entries_posted_fields",
        ),
    )
    op.create_index("ix_journal_entries_transaction_date", "journal_entries", ["transaction_date"])
    op.create_index("ix_journal_entries_status", "journal_entries", ["status"])
    op.create_index("ix_journal_entries_status_posting_date", "journal_entries", ["status", "posting_date"])

    op.create_table(
        "journal_lines",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "journal_entry_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("journal_entries.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Uuid(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("debit", sa.Numeric(18, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("credit", sa.Numeric(18, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("tax_name", sa.String(length=60), nullable=True),
        sa.Column("tax_type", sa.String(length=30), nullable=True),
        sa.Column("tax_rate", sa.Numeric(7, 4), nullable=True),
        sa.Column("tax_amount", sa.Numeric(18, 2), nullable=True),
        *_ts(),
        sa.CheckConstraint("debit >= 0", name="ck_journal_lines_debit_nonnegative"),
        sa.CheckConstraint("credit >= 0", name="ck_journal_lines_credit_nonnegative"),
        sa.CheckConstraint("(debit > 0) <> (credit > 0)", name="ck_journal_lines_one_side"),
        sa.CheckConstraint("line_number > 0", name="ck_journal_lines_line_number"),
        sa.CheckConstraint("tax_rate IS NULL OR tax_rate >= 0", name="ck_journal_lines_tax_rate"),
        sa.CheckConstraint("tax_amount IS NULL OR tax_amount >= 0", name="ck_journal_lines_tax_amount"),
    )
    op.create_index("ix_journal_lines_account", "journal_lines", ["account_id"])
    op.create_index("ix_journal_lines_entry", "journal_lines", ["journal_entry_id"])
    op.create_index(
        "uq_journal_lines_entry_line", "journal_lines", ["journal_entry_id", "line_number"], unique=True
    )

    # Entry numbers are assigned only at posting, from a sequence: gaps are possible if a posting fails.
    op.execute("CREATE SEQUENCE journal_entry_number_seq START 1 MINVALUE 1")

    # ---- guards -------------------------------------------------------------------------

    op.execute(
        """
        CREATE OR REPLACE FUNCTION logu_guard_journal_entry() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status = 'posted' THEN
                    RAISE EXCEPTION 'posted journal entries cannot be deleted; post a reversal instead'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;
                RETURN OLD;
            END IF;

            IF OLD.status = 'posted' THEN
                RAISE EXCEPTION 'posted journal entries are immutable; post a reversal instead'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;

            IF NEW.status = 'posted' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM accounting_periods p
                    WHERE p.status = 'open'
                      AND NEW.posting_date BETWEEN p.start_date AND p.end_date
                ) THEN
                    RAISE EXCEPTION 'no OPEN accounting period covers the posting date'
                        USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER journal_entries_guard
        BEFORE UPDATE OR DELETE ON journal_entries
        FOR EACH ROW EXECUTE FUNCTION logu_guard_journal_entry();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION logu_guard_journal_line() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            parent_status text;
        BEGIN
            IF TG_OP IN ('UPDATE', 'DELETE') THEN
                SELECT status INTO parent_status FROM journal_entries WHERE id = OLD.journal_entry_id;
                IF parent_status = 'posted' THEN
                    RAISE EXCEPTION 'lines of a posted journal entry are immutable'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;
            END IF;
            IF TG_OP IN ('INSERT', 'UPDATE') THEN
                SELECT status INTO parent_status FROM journal_entries WHERE id = NEW.journal_entry_id;
                IF parent_status = 'posted' THEN
                    RAISE EXCEPTION 'lines cannot be added to a posted journal entry'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER journal_lines_guard
        BEFORE INSERT OR UPDATE OR DELETE ON journal_lines
        FOR EACH ROW EXECUTE FUNCTION logu_guard_journal_line();
        """
    )

    # Deferred to COMMIT so the lines of a draft can be inserted before the entry is posted.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION logu_check_posted_balance() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            line_count integer;
            total_debit numeric(18,2);
            total_credit numeric(18,2);
        BEGIN
            SELECT count(*), COALESCE(sum(debit), 0), COALESCE(sum(credit), 0)
              INTO line_count, total_debit, total_credit
              FROM journal_lines WHERE journal_entry_id = NEW.id;
            IF line_count < 2 THEN
                RAISE EXCEPTION 'a posted journal entry needs at least two lines'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF total_debit <> total_credit THEN
                RAISE EXCEPTION 'posted journal entry is unbalanced'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER journal_entries_balanced_on_post
        AFTER INSERT OR UPDATE ON journal_entries
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW WHEN (NEW.status = 'posted')
        EXECUTE FUNCTION logu_check_posted_balance();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION logu_guard_account() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'accounts cannot be deleted; deactivate the account instead'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF OLD.is_system AND (NEW.code <> OLD.code OR NEW.category <> OLD.category) THEN
                RAISE EXCEPTION 'system account code and category are fixed'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF (NEW.code <> OLD.code OR NEW.category <> OLD.category)
               AND EXISTS (SELECT 1 FROM journal_lines WHERE account_id = OLD.id) THEN
                RAISE EXCEPTION 'code and category of an account with postings cannot change'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER accounts_guard
        BEFORE UPDATE OR DELETE ON accounts
        FOR EACH ROW EXECUTE FUNCTION logu_guard_account();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION logu_guard_period() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'accounting periods cannot be deleted'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF OLD.status = 'closed' THEN
                RAISE EXCEPTION 'a closed accounting period cannot be changed or reopened'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF NEW.start_date <> OLD.start_date OR NEW.end_date <> OLD.end_date THEN
                RAISE EXCEPTION 'period dates cannot be changed once created'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER accounting_periods_guard
        BEFORE UPDATE OR DELETE ON accounting_periods
        FOR EACH ROW EXECUTE FUNCTION logu_guard_period();
        """
    )


def downgrade() -> None:
    # Destructive rollback is deliberately unsupported: accounting history is never dropped by a
    # migration. Restore from a verified backup instead (docs/DEPLOYMENT.md).
    raise RuntimeError(
        "Downgrading the accounting schema is not supported. Restore from a verified backup instead."
    )
