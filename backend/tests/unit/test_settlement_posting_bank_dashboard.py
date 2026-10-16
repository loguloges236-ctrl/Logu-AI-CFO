"""Unit tests (no database): settlement journal lines, reconciliation near-misses, dashboard figures."""

import unittest
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.bank.reconciliation import BankLine, LedgerLine, reconcile
from app.finance.dashboard import DashboardInputs, build_dashboard
from app.finance.domain import FinanceRuleViolation, ForecastWeek
from app.settlements.posting import assert_balanced, posting_key, settlement_journal

CASH = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
AR = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
AP = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


def _sum(lines):
    return sum(line.debit for line in lines), sum(line.credit for line in lines)


class SettlementJournalTests(unittest.TestCase):
    def test_receipt_debits_cash_and_credits_receivable(self):
        lines = settlement_journal("receivable", Decimal("400.00"), CASH, AR)
        dr = {line.account_id: line.debit for line in lines if line.debit}
        cr = {line.account_id: line.credit for line in lines if line.credit}
        self.assertEqual(dr, {CASH: Decimal("400.00")})
        self.assertEqual(cr, {AR: Decimal("400.00")})
        self.assertEqual(_sum(lines)[0], _sum(lines)[1])

    def test_supplier_payment_debits_payable_and_credits_cash(self):
        lines = settlement_journal("payable", Decimal("250.50"), CASH, AP)
        dr = {line.account_id for line in lines if line.debit}
        self.assertEqual(dr, {AP})
        self.assertEqual(_sum(lines), (Decimal("250.50"), Decimal("250.50")))

    def test_reversal_swaps_sides_and_stays_balanced(self):
        original = settlement_journal("receivable", Decimal("400.00"), CASH, AR)
        reversal = settlement_journal("receivable", Decimal("-400.00"), CASH, AR)
        self.assertEqual({(line.account_id, line.debit, line.credit) for line in reversal},
                         {(line.account_id, line.credit, line.debit) for line in original})
        assert_balanced(reversal)

    def test_invalid_inputs_are_refused(self):
        with self.assertRaises(FinanceRuleViolation):
            settlement_journal("receivable", Decimal("0"), CASH, AR)
        with self.assertRaises(FinanceRuleViolation):
            settlement_journal("receivable", Decimal("10.001"), CASH, AR)
        with self.assertRaises(FinanceRuleViolation):
            settlement_journal("gift", Decimal("10"), CASH, AR)
        with self.assertRaises(FinanceRuleViolation):
            settlement_journal("payable", Decimal("10"), CASH, CASH)

    def test_unbalanced_lines_are_rejected(self):
        from app.settlements.posting import PostingLine

        with self.assertRaises(FinanceRuleViolation):
            assert_balanced([PostingLine(CASH, Decimal("10"), Decimal("0")), PostingLine(AR, Decimal("0"), Decimal("9"))])

    def test_posting_key_is_stable_per_settlement(self):
        sid = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
        self.assertEqual(posting_key(sid), posting_key(sid))
        self.assertTrue(posting_key(sid).startswith("settlement:"))


def _bank(i, d, amount, text):
    return BankLine(i, d, Decimal(amount), text)


def _ledger(i, d, amount, text):
    return LedgerLine(i, d, Decimal(amount), text)


class NearMissTests(unittest.TestCase):
    def test_same_amount_outside_tolerance_is_a_date_mismatch(self):
        result = reconcile(
            [_bank("b1", date(2026, 10, 1), "-5000.00", "rent october")],
            [_ledger("l1", date(2026, 10, 12), "-5000.00", "rent october")],
        )
        self.assertEqual(result.matched, [])
        self.assertEqual(result.near_misses, [("b1", "date_mismatch", "l1")])
        self.assertEqual(result.unmatched_ledger, ["l1"])

    def test_same_date_and_text_but_different_amount_is_an_amount_mismatch(self):
        result = reconcile(
            [_bank("b1", date(2026, 10, 1), "-5000.00", "hdfc emi loan")],
            [_ledger("l1", date(2026, 10, 1), "-4500.00", "hdfc emi loan")],
        )
        self.assertEqual(result.near_misses, [("b1", "amount_mismatch", "l1")])

    def test_unrelated_entries_are_not_near_misses(self):
        result = reconcile(
            [_bank("b1", date(2026, 10, 1), "-100.00", "fuel")],
            [_ledger("l1", date(2026, 10, 1), "-900.00", "software licence")],
        )
        self.assertEqual(result.near_misses, [])
        self.assertEqual(result.missing_in_ledger, ["b1"])

    def test_near_misses_never_create_a_match(self):
        result = reconcile(
            [_bank("b1", date(2026, 10, 1), "-5000.00", "rent")],
            [_ledger("l1", date(2026, 10, 20), "-5000.00", "rent")],
        )
        self.assertEqual(result.matched, [])


def _inputs(**overrides):
    base = dict(
        cash_available=Decimal("50000.00"),
        bank_balance=Decimal("52000.00"),
        receivables_open=Decimal("12000.00"),
        receivables_overdue=Decimal("3000.00"),
        payables_open=Decimal("8000.00"),
        payables_overdue=Decimal("0"),
        debt_outstanding=Decimal("200000.00"),
        monthly_obligations=Decimal("30000.00"),
        monthly_income=Decimal("100000.00"),
        monthly_expenses=Decimal("64000.00"),
        next_seven_days_outflow=Decimal("20000.00"),
        overdue_count=1,
        overdue_total=Decimal("3000.00"),
        unreconciled_count=2,
        forecast=[ForecastWeek(1, date(2026, 10, 14), Decimal("0"), Decimal("0"), Decimal("50000.00"), False)],
    )
    base.update(overrides)
    return DashboardInputs(**base)


class DashboardTests(unittest.TestCase):
    def test_figures_are_strings_and_net_cash_flow_is_exact(self):
        out = build_dashboard(_inputs())
        self.assertEqual(out["net_cash_flow"], "36000.00")
        self.assertEqual(out["debt_burden"], "0.3000")
        self.assertEqual(out["bank_balance"], "52000.00")
        self.assertEqual(out["unreconciled_count"], 2)

    def test_alerts_come_from_the_deterministic_rules(self):
        codes = {a["code"] for a in build_dashboard(_inputs())["alerts"]}
        self.assertNotIn("no_cash", codes)
        self.assertNotIn("cash_short_next_7_days", codes)

    def test_shortfall_and_negative_forecast_raise_critical_alerts(self):
        out = build_dashboard(_inputs(
            cash_available=Decimal("5000.00"),
            next_seven_days_outflow=Decimal("20000.00"),
            forecast=[ForecastWeek(2, date(2026, 10, 21), Decimal("0"), Decimal("0"), Decimal("-100.00"), True)],
        ))
        codes = {a["code"] for a in out["alerts"]}
        self.assertIn("cash_short_next_7_days", codes)
        self.assertIn("forecast_negative", codes)
        self.assertTrue(any(a["severity"] == "critical" for a in out["alerts"]))

    def test_negative_inputs_are_refused(self):
        with self.assertRaises(ValueError):
            build_dashboard(_inputs(receivables_open=Decimal("-1")))

    def test_no_bank_connection_reports_none_not_zero(self):
        self.assertIsNone(build_dashboard(_inputs(bank_balance=None))["bank_balance"])


if __name__ == "__main__":
    unittest.main()
