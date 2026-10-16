"""Read-only bank boundary and deterministic reconciliation. No database needed."""

import unittest
from datetime import date
from decimal import Decimal

from app.bank import providers as p
from app.bank import reconciliation as r


class ReadOnlyProviderTests(unittest.TestCase):
    def test_interface_exposes_only_read_methods(self):
        for cls in (p.BankDataProvider, p.UnavailableBankProvider, p.FixtureBankProvider):
            self.assertEqual(p.provider_method_names(cls) & p.READ_ONLY_METHODS, p.READ_ONLY_METHODS - {"name"} & p.provider_method_names(cls))
        self.assertEqual(p.READ_ONLY_METHODS, frozenset({"list_accounts", "list_transactions"}))

    def test_no_write_like_method_names_on_any_provider(self):
        for cls in (p.BankDataProvider, p.UnavailableBankProvider, p.FixtureBankProvider):
            for name in p.provider_method_names(cls):
                self.assertFalse(p.write_like(name), f"{cls.__name__}.{name} looks like a write operation")

    def test_unavailable_provider_refuses_every_call(self):
        provider = p.UnavailableBankProvider()
        with self.assertRaises(p.BankProviderUnavailable):
            provider.list_accounts()
        with self.assertRaises(p.BankProviderUnavailable):
            provider.list_transactions("acc", date(2026, 10, 1), date(2026, 10, 31))

    def test_fixture_provider_serves_only_its_data_within_range(self):
        txn = p.ProviderTransaction("t1", date(2026, 10, 5), Decimal("-100.00"), "Electricity", None)
        late = p.ProviderTransaction("t2", date(2026, 11, 5), Decimal("50.00"), "Refund", None)
        provider = p.FixtureBankProvider([], {"acc": [late, txn]})
        rows = provider.list_transactions("acc", date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual([row.external_id for row in rows], ["t1"])
        self.assertEqual(provider.list_transactions("other", date(2026, 1, 1), date(2026, 12, 31)), [])

    def test_fixture_provider_is_named_as_a_test_adapter(self):
        self.assertIn("test", p.FixtureBankProvider.name)


class ReconciliationTests(unittest.TestCase):
    def test_exact_amount_within_tolerance_matches(self):
        result = r.reconcile(
            [r.BankLine("b1", date(2026, 10, 5), Decimal("-250.00"), "Electricity board payment")],
            [r.LedgerLine("l1", date(2026, 10, 6), Decimal("-250.00"), "Electricity board")],
        )
        self.assertEqual([(s.bank_id, s.ledger_id) for s in result.matched], [("b1", "l1")])
        self.assertEqual(result.missing_in_ledger, [])
        self.assertEqual(result.unmatched_ledger, [])

    def test_outside_tolerance_is_missing_in_ledger(self):
        result = r.reconcile(
            [r.BankLine("b1", date(2026, 10, 5), Decimal("-250.00"), "x")],
            [r.LedgerLine("l1", date(2026, 10, 20), Decimal("-250.00"), "x")],
        )
        self.assertEqual(result.missing_in_ledger, ["b1"])
        self.assertEqual(result.unmatched_ledger, ["l1"])

    def test_one_ledger_line_is_never_matched_twice(self):
        result = r.reconcile(
            [r.BankLine("b1", date(2026, 10, 5), Decimal("-100"), "alpha supplier"),
             r.BankLine("b2", date(2026, 10, 5), Decimal("-100"), "beta vendor")],
            [r.LedgerLine("l1", date(2026, 10, 5), Decimal("-100"), "alpha supplier")],
        )
        self.assertEqual(len(result.matched), 1)
        self.assertEqual(len(result.missing_in_ledger), 1)

    def test_identical_bank_lines_are_flagged_as_possible_duplicates(self):
        result = r.reconcile(
            [r.BankLine("b1", date(2026, 10, 5), Decimal("-40"), "Fuel station"),
             r.BankLine("b2", date(2026, 10, 5), Decimal("-40"), "Fuel station")],
            [r.LedgerLine("l1", date(2026, 10, 5), Decimal("-40"), "Fuel station")],
        )
        self.assertEqual(result.possible_duplicates, ["b2"])
        self.assertEqual([s.bank_id for s in result.matched], ["b1"])

    def test_equally_good_candidates_are_ambiguous_not_guessed(self):
        result = r.reconcile(
            [r.BankLine("b1", date(2026, 10, 5), Decimal("-75"), "payment")],
            [r.LedgerLine("l1", date(2026, 10, 5), Decimal("-75"), "payment"),
             r.LedgerLine("l2", date(2026, 10, 5), Decimal("-75"), "payment")],
        )
        self.assertEqual(result.matched, [])
        self.assertEqual([s.status for s in result.ambiguous], ["ambiguous"])
        self.assertEqual(result.unmatched_ledger, ["l1", "l2"])

    def test_description_breaks_ties_between_dates(self):
        result = r.reconcile(
            [r.BankLine("b1", date(2026, 10, 5), Decimal("-300"), "salary ravi")],
            [r.LedgerLine("l1", date(2026, 10, 5), Decimal("-300"), "rent office"),
             r.LedgerLine("l2", date(2026, 10, 5), Decimal("-300"), "salary ravi october")],
        )
        self.assertEqual([(s.bank_id, s.ledger_id) for s in result.matched], [("b1", "l2")])

    def test_reconciliation_is_deterministic(self):
        bank = [r.BankLine(f"b{i}", date(2026, 10, 1 + i % 5), Decimal(-10 * (i % 3 + 1)), "x y") for i in range(12)]
        ledger = [r.LedgerLine(f"l{i}", date(2026, 10, 1 + i % 5), Decimal(-10 * (i % 3 + 1)), "x") for i in range(12)]
        self.assertEqual(r.reconcile(bank, ledger), r.reconcile(list(reversed(bank)), list(reversed(ledger))))


if __name__ == "__main__":
    unittest.main()
