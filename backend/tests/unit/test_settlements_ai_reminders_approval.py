"""Unit tests (no database): settlement status, read-only AI executor, reminder cycle, document approval."""

import unittest
from datetime import date
from decimal import Decimal

from app.ai.cfo import AIRuleViolation
from app.ai.executor import READER_METHOD, CFOReader, execute_tool
from app.finance.approval import draft_obligation
from app.finance.domain import FinanceRuleViolation
from app.notifications.domain import DuePayment, InAppChannel, NoExternalChannel
from app.notifications.scheduler import run_reminder_cycle
from app.settlements.domain import settlement_status

TODAY = date(2026, 10, 8)


class SettlementStatusTests(unittest.TestCase):
    def test_unpaid_partial_and_paid(self):
        self.assertEqual(settlement_status(Decimal("1000.00"), Decimal("0")).status, "unpaid")
        partial = settlement_status(Decimal("1000.00"), Decimal("400.00"))
        self.assertEqual((partial.status, partial.outstanding), ("partially_paid", Decimal("600.00")))
        self.assertEqual(settlement_status(Decimal("1000.00"), Decimal("1000.00")).status, "paid")

    def test_overpayment_and_negative_values_are_refused(self):
        with self.assertRaises(FinanceRuleViolation):
            settlement_status(Decimal("100.00"), Decimal("100.01"))
        with self.assertRaises(FinanceRuleViolation):
            settlement_status(Decimal("100.00"), Decimal("-1.00"))
        with self.assertRaises(FinanceRuleViolation):
            settlement_status(Decimal("-1.00"), Decimal("0"))


class FakeReader:
    """Records calls. It exposes only the CFOReader methods."""

    def __init__(self):
        self.calls = []

    def _record(self, name, *args):
        self.calls.append((name, args))
        return {"amount": Decimal("12.50"), "items": [{"x": Decimal("1")}]}

    def cash_position(self): return self._record("cash_position")
    def upcoming_payments(self, scope): return self._record("upcoming_payments", scope)
    def receivables(self): return self._record("receivables")
    def payables(self): return self._record("payables")
    def debt_summary(self): return self._record("debt_summary")
    def debt_strategy(self, monthly_budget): return self._record("debt_strategy", monthly_budget)
    def period_summary(self, metric): return self._record("period_summary", metric)
    def top_expenses(self): return self._record("top_expenses")
    def cash_movement(self): return self._record("cash_movement")
    def risk_alerts(self): return self._record("risk_alerts")
    def priority_list(self): return self._record("priority_list")
    def reconciliation_status(self): return self._record("reconciliation_status")


WRITE_VERBS = ("post", "pay", "transfer", "create", "update", "delete", "insert", "reverse", "execute", "sql", "write")


class AIExecutorTests(unittest.TestCase):
    def test_every_registered_tool_maps_to_a_reader_method_and_back(self):
        from app.ai.cfo import TOOLS

        self.assertEqual(set(READER_METHOD), set(TOOLS))
        protocol_methods = {m for m in dir(CFOReader) if not m.startswith("_")}
        self.assertEqual(set(READER_METHOD.values()), protocol_methods)

    def test_reader_surface_has_no_write_verbs(self):
        for name in READER_METHOD.values():
            self.assertFalse(set(name.split("_")) & set(WRITE_VERBS), name)

    def test_executes_a_read_only_tool_and_returns_json_safe_values(self):
        reader = FakeReader()
        out = execute_tool("finance.upcoming_payments", {"scope": "week"}, reader)
        self.assertEqual(reader.calls, [("upcoming_payments", ("week",))])
        self.assertEqual(out["result"]["amount"], "12.50")
        self.assertEqual(out["result"]["items"][0]["x"], "1")

    def test_defaults_scope_and_metric(self):
        reader = FakeReader()
        execute_tool("finance.upcoming_payments", {}, reader)
        execute_tool("finance.period_summary", {}, reader)
        self.assertEqual(reader.calls[0], ("upcoming_payments", ("all",)))
        self.assertEqual(reader.calls[1], ("period_summary", ("profit",)))

    def test_unknown_tool_and_write_tools_are_refused(self):
        reader = FakeReader()
        for name in ("finance.post_journal", "db.execute", "payments.pay", "inventory.adjust"):
            with self.assertRaises(AIRuleViolation):
                execute_tool(name, {}, reader)
        self.assertEqual(reader.calls, [])

    def test_sql_and_unlisted_arguments_are_refused(self):
        reader = FakeReader()
        for args in ({"sql": "select 1"}, {"query": "x"}, {"scope": "week", "where": "1=1"}, {"amount": "5"}):
            with self.assertRaises(AIRuleViolation):
                execute_tool("finance.upcoming_payments", args, reader)
        self.assertEqual(reader.calls, [])

    def test_invalid_values_are_refused(self):
        reader = FakeReader()
        with self.assertRaises(AIRuleViolation):
            execute_tool("finance.upcoming_payments", {"scope": "drop table"}, reader)
        with self.assertRaises(AIRuleViolation):
            execute_tool("finance.period_summary", {"metric": "cash"}, reader)
        with self.assertRaises(AIRuleViolation):
            execute_tool("finance.debt_strategy", {"monthly_budget": "-5"}, reader)
        with self.assertRaises(AIRuleViolation):
            execute_tool("finance.debt_strategy", {"monthly_budget": "lots"}, reader)
        self.assertEqual(reader.calls, [])

    def test_valid_budget_is_normalised(self):
        reader = FakeReader()
        execute_tool("finance.debt_strategy", {"monthly_budget": "25000.50"}, reader)
        self.assertEqual(reader.calls, [("debt_strategy", ("25000.50",))])


def _due(key, days, paid=False, kind="emi"):
    from datetime import timedelta

    return DuePayment(key, kind, f"Item {key}", TODAY + timedelta(days=days), Decimal("1000.00"), paid)


class ReminderCycleTests(unittest.TestCase):
    def test_running_the_cycle_twice_stores_nothing_new(self):
        stored: set[str] = set()

        def persist(message):
            if message.dedupe_key in stored:
                return False
            stored.add(message.dedupe_key)
            return True

        channel = InAppChannel(persist)
        payments = [_due("a", 0), _due("b", 3), _due("c", -2), _due("d", 30), _due("e", 1, paid=True)]
        first = run_reminder_cycle(payments, TODAY, channel)
        second = run_reminder_cycle(payments, TODAY, channel)
        self.assertEqual((first.planned, first.delivered), (3, 3))
        self.assertEqual((second.planned, second.delivered, second.not_delivered), (3, 0, 3))
        self.assertEqual(first.kinds, {"emi_due": 3})

    def test_without_an_external_provider_nothing_is_reported_as_sent(self):
        report = run_reminder_cycle([_due("a", 0)], TODAY, NoExternalChannel())
        self.assertEqual((report.planned, report.delivered, report.not_delivered), (1, 0, 1))


class DocumentApprovalTests(unittest.TestCase):
    EMI = {
        "party_name": "HDFC Bank",
        "reference_number": "EMI-0042",
        "document_date": "2026-10-01",
        "due_date": "2026-10-10",
        "grand_total": "12500.00",
    }
    CONF = {k: Decimal("0.95") for k in EMI}

    def test_confident_emi_becomes_a_once_obligation(self):
        result = draft_obligation("emi", dict(self.EMI), dict(self.CONF))
        self.assertEqual(result.status, "ready")
        ob = result.obligation
        self.assertEqual((ob.kind, ob.frequency, ob.amount, ob.first_due_date), ("emi", "once", Decimal("12500.00"), date(2026, 10, 10)))
        self.assertEqual(ob.title, "EMI EMI-0042 - HDFC Bank")
        self.assertTrue(ob.is_critical)

    def test_low_confidence_goes_to_review_and_creates_nothing(self):
        conf = dict(self.CONF, grand_total=Decimal("0.40"))
        result = draft_obligation("emi", dict(self.EMI), conf)
        self.assertEqual(result.status, "needs_review")
        self.assertIsNone(result.obligation)
        self.assertIn("low_confidence", {issue["code"] for issue in result.issues})

    def test_zero_or_negative_amount_is_refused_for_review(self):
        payload = dict(self.EMI, grand_total="0")
        result = draft_obligation("emi", payload, dict(self.CONF))
        self.assertEqual(result.status, "needs_review")
        self.assertIn("invalid_amount", {issue["code"] for issue in result.issues})

    def test_salary_uses_document_date_and_is_monthly(self):
        payload = {"party_name": "R. Kumar", "document_date": "2026-10-07", "grand_total": "30000.00"}
        result = draft_obligation("salary", payload, {k: Decimal("0.9") for k in payload})
        self.assertEqual(result.status, "ready")
        self.assertEqual((result.obligation.kind, result.obligation.frequency), ("salary", "monthly"))
        self.assertEqual(result.obligation.first_due_date, date(2026, 10, 7))

    def test_interest_document(self):
        payload = {"party_name": "HDFC Bank", "document_date": "2026-10-01", "due_date": "2026-10-05", "grand_total": "850.00"}
        result = draft_obligation("interest", payload, {k: Decimal("0.9") for k in payload})
        self.assertEqual((result.status, result.obligation.kind), ("ready", "interest"))

    def test_non_finance_documents_are_not_turned_into_obligations(self):
        with self.assertRaises(FinanceRuleViolation):
            draft_obligation("purchase_bill", {}, {})


if __name__ == "__main__":
    unittest.main()
