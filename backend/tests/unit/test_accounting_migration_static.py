"""Static checks on the accounting migration text (no database needed).

Runtime verification of the DDL and triggers is in tests/integration/test_accounting_flows.py
and tests/integration/test_migrations.py, which require PostgreSQL.
"""

import pathlib
import re
import unittest

MIGRATION = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions" / "20261008_0002_accounting.py"


class AccountingMigrationStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = MIGRATION.read_text(encoding="utf-8")

    def test_revision_chain_follows_phase_one(self):
        self.assertIn('revision = "20261008_0002"', self.text)
        self.assertIn('down_revision = "20261007_0001"', self.text)

    def test_no_create_all_or_destructive_ddl(self):
        for forbidden in ("create_all", "drop_all", "DROP TABLE", "DROP SCHEMA", "TRUNCATE "):
            self.assertNotIn(forbidden, self.text, forbidden)

    def test_downgrade_is_refused(self):
        self.assertRegex(self.text, r"def downgrade\(\) -> None:[\s\S]*raise RuntimeError")

    def test_money_columns_are_numeric_18_2(self):
        self.assertGreaterEqual(len(re.findall(r"sa\.Numeric\(18, 2\)", self.text)), 2)
        self.assertNotIn("sa.Float", self.text)
        self.assertNotIn("sa.REAL", self.text)

    def test_immutability_and_balance_triggers_exist(self):
        for name in (
            "journal_entries_guard",
            "journal_lines_guard",
            "journal_entries_balanced_on_post",
            "accounts_guard",
            "accounting_periods_guard",
        ):
            self.assertIn(name, self.text, name)
        self.assertIn("CONSTRAINT TRIGGER journal_entries_balanced_on_post", self.text)
        self.assertIn("DEFERRABLE INITIALLY DEFERRED", self.text)

    def test_period_overlap_is_excluded_at_database_level(self):
        self.assertIn("EXCLUDE USING gist", self.text)

    def test_entry_number_sequence_is_created(self):
        self.assertIn("CREATE SEQUENCE journal_entry_number_seq", self.text)


if __name__ == "__main__":
    unittest.main()


class EnumDriftTests(unittest.TestCase):
    """The migration's literal enum lists must equal the domain module's lists (no silent drift)."""

    @classmethod
    def setUpClass(cls):
        cls.text = MIGRATION.read_text(encoding="utf-8")

    def _tuple(self, name):
        match = re.search(rf"^{name} = \((.*?)\)$", self.text, flags=re.S | re.M)
        self.assertIsNotNone(match, name)
        return set(re.findall(r'"([a-z_]+)"', match.group(1)))

    def test_categories_match_domain(self):
        from app.accounting.domain import AccountCategory

        self.assertEqual(self._tuple("_CATEGORIES"), {c.value for c in AccountCategory})

    def test_subtypes_match_domain(self):
        from app.accounting.domain import ACCOUNT_SUBTYPES

        self.assertEqual(self._tuple("_SUBTYPES"), set(ACCOUNT_SUBTYPES))

    def test_period_and_entry_statuses_match_domain(self):
        from app.accounting.domain import EntryStatus, PeriodStatus

        self.assertEqual(self._tuple("_PERIOD_STATUSES"), {s.value for s in PeriodStatus})
        self.assertEqual(self._tuple("_ENTRY_STATUSES"), {s.value for s in EntryStatus})
