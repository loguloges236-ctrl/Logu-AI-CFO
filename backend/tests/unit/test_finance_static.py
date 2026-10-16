"""Static drift checks for the finance migration (no database, no SQLAlchemy needed).

- Every table in the finance migration has an ORM model with the same column names.
- Literal enum lists in the migration equal the Python constants the services use.
- The migration is explicit: no create_all, drop_all, DROP TABLE or DROP SCHEMA; downgrade is refused.
"""

import pathlib
import re
import unittest

BACKEND = pathlib.Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "alembic" / "versions" / "20261008_0004_finance_bank.py"
SETTLEMENTS_MIGRATION = BACKEND / "alembic" / "versions" / "20261008_0005_settlements.py"
LATER_MIGRATIONS = (
    BACKEND / "alembic" / "versions" / "20261008_0006_settlement_journal_link.py",
    BACKEND / "alembic" / "versions" / "20261008_0007_finance_documents.py",
    BACKEND / "alembic" / "versions" / "20261008_0008_finance_lifecycle.py",
)
MODELS = BACKEND / "app" / "models" / "finance.py"


def _migration_tables(text: str) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for match in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\s*\)", text, re.S):
        name, body = match.group(1), match.group(2)
        lines = [ln for ln in body.split("\n") if ln.strip()]
        base = len(lines[0]) - len(lines[0].lstrip())
        columns: set[str] = set()
        for raw in lines:
            indent = len(raw) - len(raw.lstrip())
            stripped = raw.strip()
            if indent != base or stripped.startswith(("CONSTRAINT", "UNIQUE", "CHECK", "PRIMARY", "FOREIGN")):
                continue
            if stripped.startswith("{_ts()}") or "{_ts()}" in stripped:
                columns.update({"created_at", "updated_at"})
                continue
            word = re.match(r"(\w+)\s", stripped + " ")
            if word:
                columns.add(word.group(1))
        tables[name] = columns
    return tables


def _model_tables(text: str) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for cls in re.finditer(r'class (\w+)\(Base\):\n(?:\s+\"\"\".*?\"\"\"\n)?\s+__tablename__ = "(\w+)"\n(.*?)(?=\nclass |\Z)', text, re.S):
        tables[cls.group(2)] = set(re.findall(r"^    (\w+): Mapped", cls.group(3), re.M))
    return tables


class FinanceMigrationStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.migration = MIGRATION.read_text(encoding="utf-8")
        cls.settlements = SETTLEMENTS_MIGRATION.read_text(encoding="utf-8")
        cls.models = MODELS.read_text(encoding="utf-8")

    def test_every_migration_table_has_matching_orm_columns(self) -> None:
        lifecycle = (BACKEND / "alembic" / "versions" / "20261008_0008_finance_lifecycle.py").read_text(encoding="utf-8")
        migration = _migration_tables(self.migration + self.settlements + lifecycle)
        for path in LATER_MIGRATIONS:
            for table, column in re.findall(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", path.read_text(encoding="utf-8")):
                migration.setdefault(table, set()).add(column)
        models = _model_tables(self.models)
        self.assertEqual(set(migration), set(models), "table sets differ")
        for name, columns in migration.items():
            self.assertEqual(columns, models[name], f"column drift in {name}")

    def test_enum_literals_match_python_constants(self) -> None:
        from app.bank.reconciliation import SUGGESTION_KINDS
        from app.commerce.extraction import DOCUMENT_TYPES
        from app.finance.domain import FREQUENCIES, INTEREST_TYPES, OBLIGATION_KINDS
        from app.notifications.domain import KINDS

        latest = (BACKEND / "alembic" / "versions" / "20261008_0007_finance_documents.py").read_text(encoding="utf-8")
        block = re.search(r"DOCUMENT_TYPES = \((.*?)\)\n", latest, re.S)
        self.assertIsNotNone(block)
        self.assertEqual(set(re.findall(r'"(\w+)"', block.group(1))), set(DOCUMENT_TYPES))
        kinds = re.search(r"SUGGESTION_KINDS = \((.*?)\)\n", latest, re.S)
        from app.bank.reconciliation import SUGGESTION_KINDS as RECON_KINDS
        self.assertEqual(set(re.findall(r'"(\w+)"', kinds.group(1))), set(RECON_KINDS))
        for const, expected in (
            ("OBLIGATION_KINDS", OBLIGATION_KINDS),
            ("FREQUENCIES", FREQUENCIES),
            ("INTEREST_TYPES", INTEREST_TYPES),
            ("NOTIFICATION_KINDS", KINDS),
        ):
            match = re.search(rf"{const} = \((.*?)\)\n", self.migration, re.S)
            self.assertIsNotNone(match, const)
            self.assertEqual(set(re.findall(r'"(\w+)"', match.group(1))), set(expected), const)

    def test_migration_is_explicit_and_refuses_downgrade(self) -> None:
        for text in (self.migration, self.settlements):
            for forbidden in ("create_all", "drop_all", "DROP TABLE", "DROP SCHEMA"):
                self.assertNotIn(forbidden, text)
            self.assertIn("raise RuntimeError", text)

    def test_settlement_literals_match_the_domain(self) -> None:
        from app.settlements.domain import PAYMENT_STATUSES

        block = re.search(r"METHODS = \((.*?)\)\n", self.settlements, re.S)
        self.assertIsNotNone(block)
        self.assertEqual(set(re.findall(r'"(\w+)"', block.group(1))), {"cash", "bank", "upi", "cheque", "card", "other"})
        self.assertEqual(PAYMENT_STATUSES, ("unpaid", "partially_paid", "paid"))

    def test_append_only_tables_have_triggers(self) -> None:
        for table in ("payment_records", "bank_transactions"):
            self.assertRegex(self.migration, rf"{table}_append_only")
            self.assertRegex(self.migration, rf"{table}_no_truncate")
        self.assertRegex(self.migration, "logu_finance_payment_cap")
        self.assertRegex(self.migration, "FOR UPDATE")


if __name__ == "__main__":
    unittest.main()
