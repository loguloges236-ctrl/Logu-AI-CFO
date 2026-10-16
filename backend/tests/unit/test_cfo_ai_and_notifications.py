"""Controlled AI layer (tool authorization, read-only, blocked writes and SQL) and reminder planning."""

import unittest
from datetime import date
from decimal import Decimal

from app.ai import cfo
from app.notifications import domain as n


class ToolAuthorizationTests(unittest.TestCase):
    def test_registry_passes_the_phase_two_read_only_guard(self):
        cfo.assert_registry_is_read_only()

    def test_registered_tool_with_declared_args_is_allowed(self):
        self.assertEqual(cfo.authorize_call("finance.upcoming_payments", {"scope": "week"}).name, "finance.upcoming_payments")

    def test_unknown_tool_is_refused(self):
        with self.assertRaises(cfo.AIRuleViolation) as ctx:
            cfo.authorize_call("finance.pay_supplier", {})
        self.assertEqual(ctx.exception.code, "unknown_tool")

    def test_undeclared_argument_is_refused(self):
        with self.assertRaises(cfo.AIRuleViolation):
            cfo.authorize_call("finance.cash_position", {"scope": "week"})

    def test_sql_like_and_write_like_arguments_are_refused(self):
        for key in ("sql", "query", "table", "where", "amount", "account_id", "post"):
            with self.assertRaises(cfo.AIRuleViolation, msg=key):
                cfo.authorize_call("finance.upcoming_payments", {key: "week"})

    def test_argument_values_must_be_short_plain_text(self):
        for value in ("week; drop table x", "x" * 50, "a'b", 5):
            with self.assertRaises(cfo.AIRuleViolation, msg=repr(value)):
                cfo.authorize_call("finance.upcoming_payments", {"scope": value})

    def test_no_registered_tool_is_a_write(self):
        for name in cfo.TOOLS:
            words = set(name.rsplit(".", 1)[-1].split("_"))
            for verb in ("create", "post", "reverse", "update", "delete", "pay", "transfer", "sql", "set"):
                self.assertNotIn(verb, words, name)


class QuestionRoutingTests(unittest.TestCase):
    def assertRoutes(self, question, tool):
        route = cfo.route_question(question)
        self.assertEqual(route.kind, "tool", question)
        self.assertEqual(route.tool, tool, question)

    def test_owner_questions_route_to_read_only_tools(self):
        cases = {
            "How much cash do I have?": "finance.cash_position",
            "What payments are due this week?": "finance.upcoming_payments",
            "What is overdue?": "finance.upcoming_payments",
            "How much do customers owe me?": "finance.receivables",
            "How much do I owe suppliers?": "finance.payables",
            "Which debt should I pay first?": "finance.debt_strategy",
            "What were my expenses this month?": "finance.period_summary",
            "Why did my cash reduce?": "finance.cash_movement",
            "How much did I sell?": "finance.period_summary",
            "What is my profit?": "finance.period_summary",
            "Show my biggest expenses.": "finance.top_expenses",
            "What financial risks do I have?": "finance.risk_alerts",
        }
        for question, tool in cases.items():
            self.assertRoutes(question, tool)

    def test_money_movement_requests_are_refused(self):
        for question in ("Transfer 5000 to Ram", "Make a payment to the bank now", "Send money to supplier", "Pay now the EMI"):
            self.assertEqual(cfo.route_question(question).kind, "refuse", question)

    def test_write_and_sql_requests_are_refused(self):
        for question in ("Delete the last invoice", "Post the journal entry", "Reverse this", "run sql select * from accounts", "update my stock"):
            route = cfo.route_question(question)
            self.assertEqual(route.kind, "refuse", question)
            self.assertIsNone(route.tool)

    def test_unknown_question_gets_guidance_not_a_guess(self):
        route = cfo.route_question("Write me a poem about rivers")
        self.assertEqual(route.kind, "unknown")
        self.assertIsNone(route.tool)

    def test_empty_or_oversized_question_is_unknown(self):
        self.assertEqual(cfo.route_question("   ").kind, "unknown")
        self.assertEqual(cfo.route_question("x" * 301).kind, "unknown")


class ReminderTests(unittest.TestCase):
    today = date(2026, 10, 8)

    def test_overdue_and_due_soon_unpaid_items_get_messages(self):
        items = [
            n.DuePayment("e1", "emi", "Car loan EMI", date(2026, 10, 5), Decimal("8000"), False),
            n.DuePayment("r1", "rent", "Office rent", date(2026, 10, 9), Decimal("25000"), False),
            n.DuePayment("s1", "salary", "Ravi salary", date(2026, 10, 30), Decimal("30000"), False),
        ]
        messages = n.plan_reminders(items, self.today)
        self.assertEqual({m.kind for m in messages}, {"emi_due", "recurring_expense"})
        self.assertTrue(any(m.title.startswith("Overdue") for m in messages))

    def test_paid_items_get_no_reminder(self):
        items = [n.DuePayment("r1", "rent", "Office rent", date(2026, 10, 8), Decimal("25000"), True)]
        self.assertEqual(n.plan_reminders(items, self.today), [])

    def test_dedupe_keys_are_stable_so_replanning_never_duplicates(self):
        items = [n.DuePayment("r1", "rent", "Office rent", date(2026, 10, 8), Decimal("25000"), False)]
        first = n.plan_reminders(items, self.today)
        second = n.plan_reminders(items, self.today)
        self.assertEqual([m.dedupe_key for m in first], [m.dedupe_key for m in second])
        self.assertEqual(first[0].dedupe_key, "recurring_expense:r1:2026-10-08")

    def test_kinds_are_the_spec_list(self):
        self.assertEqual(
            set(n.KINDS),
            {"emi_due", "debt_payment_due", "supplier_payment_due", "customer_payment_overdue", "salary_date",
             "recurring_expense", "document_review_required", "reconciliation_issue", "financial_risk_alert"},
        )

    def test_no_external_channel_reports_nothing_delivered(self):
        msg = n.Message("emi_due", "t", "b", "k", None)
        self.assertFalse(n.NoExternalChannel().deliver(msg))

    def test_in_app_channel_delegates_to_persistence(self):
        stored = []
        channel = n.InAppChannel(lambda m: stored.append(m) or True)
        self.assertTrue(channel.deliver(n.Message("emi_due", "t", "b", "k", None)))
        self.assertEqual(len(stored), 1)


if __name__ == "__main__":
    unittest.main()
