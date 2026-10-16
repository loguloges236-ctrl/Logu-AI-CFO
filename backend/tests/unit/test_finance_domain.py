"""Pure finance rules: money, EMI and interest schedules, recurrence, buckets, payment priority, debt strategies,
cash forecast and risk alerts. No database needed."""

import unittest
from datetime import date
from decimal import Decimal

from app.finance import domain as f


class MoneyAndRateTests(unittest.TestCase):
    def test_money_must_be_a_string_with_two_places(self):
        self.assertEqual(f.parse_money("10.5", "x"), Decimal("10.50"))
        for bad in (10.5, 10, "10.555", "-1", "abc", ""):
            with self.assertRaises(f.FinanceRuleViolation, msg=repr(bad)):
                f.parse_money(bad, "x")

    def test_zero_money_is_refused_unless_allowed(self):
        with self.assertRaises(f.FinanceRuleViolation):
            f.parse_money("0", "x")
        self.assertEqual(f.parse_money("0", "x", allow_zero=True), Decimal("0.00"))

    def test_rate_bounds(self):
        self.assertEqual(f.parse_rate("8.5", "rate"), Decimal("8.5"))
        self.assertEqual(f.parse_rate("", "rate"), Decimal("0"))
        for bad in ("101", "-1", 8.5, "1.00001"):
            with self.assertRaises(f.FinanceRuleViolation, msg=repr(bad)):
                f.parse_rate(bad, "rate")

    def test_month_arithmetic_clamps_the_day(self):
        self.assertEqual(f.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(f.add_months(date(2028, 1, 31), 1), date(2028, 2, 29))
        self.assertEqual(f.add_months(date(2026, 11, 15), 3), date(2027, 2, 15))


class EmiScheduleTests(unittest.TestCase):
    def test_reducing_emi_matches_the_standard_formula(self):
        self.assertEqual(f.emi_amount(Decimal("100000"), Decimal("12"), 12, "reducing"), Decimal("8884.88"))

    def test_flat_emi(self):
        self.assertEqual(f.emi_amount(Decimal("12000"), Decimal("10"), 12, "flat"), Decimal("1100.00"))

    def test_zero_interest_loan(self):
        self.assertEqual(f.emi_amount(Decimal("1200"), Decimal("0"), 12, "reducing"), Decimal("100.00"))

    def test_schedule_clears_to_zero_and_principal_sums_to_loan(self):
        rows = f.build_schedule(Decimal("100000"), Decimal("12"), 12, "reducing", date(2026, 1, 31))
        self.assertEqual(rows[-1].closing, Decimal("0.00"))
        self.assertEqual(sum(r.principal for r in rows), Decimal("100000.00"))
        self.assertEqual(rows[0].opening, Decimal("100000.00"))
        self.assertEqual(rows[0].interest, Decimal("1000.00"))
        self.assertEqual(rows[1].opening, rows[0].closing)

    def test_each_installment_splits_into_interest_and_principal(self):
        for row in f.build_schedule(Decimal("50000"), Decimal("9.5"), 24, "reducing", date(2026, 3, 1)):
            self.assertEqual(row.emi, row.interest + row.principal)
            self.assertGreaterEqual(row.principal, 0)

    def test_flat_schedule_total_interest_is_exact(self):
        rows = f.build_schedule(Decimal("12000"), Decimal("10"), 12, "flat", date(2026, 1, 1))
        self.assertEqual(f.total_interest(rows), Decimal("1200.00"))
        self.assertEqual(rows[-1].closing, Decimal("0.00"))
        self.assertEqual(sum(r.principal for r in rows), Decimal("12000.00"))

    def test_schedule_due_dates_are_monthly(self):
        rows = f.build_schedule(Decimal("1000"), Decimal("0"), 3, "reducing", date(2026, 1, 31))
        self.assertEqual([r.due_date for r in rows], [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)])

    def test_tenure_bounds(self):
        for months in (0, f.MAX_MONTHS + 1):
            with self.assertRaises(f.FinanceRuleViolation):
                f.emi_amount(Decimal("1000"), Decimal("5"), months, "reducing")

    def test_unknown_interest_type_refused(self):
        with self.assertRaises(f.FinanceRuleViolation):
            f.emi_amount(Decimal("1000"), Decimal("5"), 12, "compound")


class RecurrenceAndBucketTests(unittest.TestCase):
    def test_monthly_occurrences_in_a_window(self):
        dates = f.occurrences(date(2026, 1, 5), "monthly", date(2026, 2, 1), date(2026, 4, 30))
        self.assertEqual(dates, [date(2026, 2, 5), date(2026, 3, 5), date(2026, 4, 5)])

    def test_recurrence_stops_at_end_date(self):
        dates = f.occurrences(date(2026, 1, 5), "monthly", date(2026, 1, 1), date(2026, 12, 31), end=date(2026, 3, 1))
        self.assertEqual(dates, [date(2026, 1, 5), date(2026, 2, 5)])

    def test_weekly_and_yearly(self):
        self.assertEqual(len(f.occurrences(date(2026, 10, 1), "weekly", date(2026, 10, 1), date(2026, 10, 31))), 5)
        self.assertEqual(f.occurrences(date(2025, 6, 1), "yearly", date(2026, 1, 1), date(2026, 12, 31)), [date(2026, 6, 1)])

    def test_once_only_in_window(self):
        self.assertEqual(f.occurrences(date(2026, 5, 1), "once", date(2026, 1, 1), date(2026, 12, 31)), [date(2026, 5, 1)])
        self.assertEqual(f.occurrences(date(2026, 5, 1), "once", date(2026, 6, 1), date(2026, 12, 31)), [])

    def test_invalid_frequency(self):
        with self.assertRaises(f.FinanceRuleViolation):
            f.occurrences(date(2026, 1, 1), "daily", date(2026, 1, 1), date(2026, 2, 1))

    def test_buckets(self):
        today = date(2026, 10, 8)
        self.assertEqual(f.bucket(date(2026, 10, 7), today), "overdue")
        self.assertEqual(f.bucket(today, today), "today")
        self.assertEqual(f.bucket(date(2026, 10, 9), today), "tomorrow")
        self.assertEqual(f.bucket(date(2026, 10, 14), today), "this_week")
        self.assertEqual(f.bucket(date(2026, 10, 20), today), "later")


class PriorityTests(unittest.TestCase):
    def test_overdue_outranks_nearer_items(self):
        today = date(2026, 10, 8)
        items = [
            f.PayItem("due_tomorrow", Decimal("500"), date(2026, 10, 9), Decimal("0"), "rent"),
            f.PayItem("overdue_small", Decimal("100"), date(2026, 10, 1), Decimal("0"), "other"),
        ]
        rows = f.prioritise(items, Decimal("1000"), today)
        self.assertEqual(rows[0].key, "overdue_small")
        self.assertIn("overdue by 7 day(s)", rows[0].reasons)

    def test_interest_cost_and_criticality_break_ties(self):
        today = date(2026, 10, 8)
        due = date(2026, 10, 15)
        items = [
            f.PayItem("cheap_card", Decimal("100"), due, Decimal("18"), "credit_card"),
            f.PayItem("emi_loan", Decimal("100"), due, Decimal("12"), "emi"),
            f.PayItem("subscription", Decimal("100"), due, Decimal("0"), "subscription"),
        ]
        keys = [r.key for r in f.prioritise(items, Decimal("1000"), today)]
        self.assertEqual(keys, ["emi_loan", "cheap_card", "subscription"])

    def test_affordability_follows_running_total(self):
        today = date(2026, 10, 8)
        items = [
            f.PayItem("a", Decimal("300"), date(2026, 10, 9), Decimal("0"), "rent"),
            f.PayItem("b", Decimal("300"), date(2026, 10, 10), Decimal("0"), "rent"),
            f.PayItem("c", Decimal("300"), date(2026, 10, 11), Decimal("0"), "rent"),
        ]
        rows = f.prioritise(items, Decimal("650"), today)
        self.assertEqual([r.affordable for r in rows], [True, True, False])
        self.assertEqual(rows[-1].cumulative, Decimal("900"))


class DebtStrategyTests(unittest.TestCase):
    def setUp(self):
        self.plans = [
            f.DebtPlan("card", Decimal("50000"), Decimal("36"), Decimal("1500")),
            f.DebtPlan("loan", Decimal("20000"), Decimal("11"), Decimal("600")),
        ]

    def test_avalanche_pays_highest_rate_first(self):
        result = f.simulate_payoff(self.plans, Decimal("5000"), "avalanche")
        self.assertEqual(result.payoff_order[0], "card")
        self.assertIsNotNone(result.months)

    def test_snowball_pays_smallest_balance_first(self):
        result = f.simulate_payoff(self.plans, Decimal("5000"), "snowball")
        self.assertEqual(result.payoff_order[0], "loan")

    def test_avalanche_costs_no_more_interest_than_snowball(self):
        comparison = f.compare_strategies(self.plans, Decimal("5000"))
        self.assertGreaterEqual(comparison["interest_saved_by_avalanche"], Decimal("0"))
        self.assertEqual(
            comparison["interest_saved_by_avalanche"],
            comparison["snowball"].total_interest - comparison["avalanche"].total_interest,
        )

    def test_budget_below_minimums_is_refused(self):
        with self.assertRaises(f.FinanceRuleViolation) as ctx:
            f.simulate_payoff(self.plans, Decimal("1000"), "avalanche")
        self.assertEqual(ctx.exception.code, "budget_below_minimums")

    def test_never_cleared_debt_reports_no_month_count(self):
        plan = [f.DebtPlan("bad", Decimal("100000"), Decimal("60"), Decimal("100"))]
        result = f.simulate_payoff(plan, Decimal("100"), "avalanche")
        self.assertIsNone(result.months)

    def test_no_debts_needs_no_months(self):
        self.assertEqual(f.simulate_payoff([], Decimal("0"), "avalanche").months, 0)

    def test_unknown_strategy_refused(self):
        with self.assertRaises(f.FinanceRuleViolation):
            f.simulate_payoff(self.plans, Decimal("5000"), "random")

    def test_total_paid_equals_principal_plus_interest_when_cleared(self):
        for strategy in ("avalanche", "snowball"):
            result = f.simulate_payoff(self.plans, Decimal("5000"), strategy)
            self.assertIsNotNone(result.months)
            self.assertEqual(result.total_paid, Decimal("70000.00") + result.total_interest)


class ForecastAndAlertTests(unittest.TestCase):
    def test_forecast_weeks_and_negative_flag(self):
        today = date(2026, 10, 8)
        weeks = f.forecast(
            Decimal("1000"),
            [f.DatedAmount(date(2026, 10, 12), Decimal("500"), "customer")],
            [f.DatedAmount(date(2026, 10, 9), Decimal("800"), "rent"), f.DatedAmount(date(2026, 10, 20), Decimal("900"), "emi")],
            today,
            3,
        )
        self.assertEqual(weeks[0].closing, Decimal("1000") + Decimal("500") - Decimal("800"))
        self.assertFalse(weeks[0].negative)
        self.assertEqual(weeks[1].outflow, Decimal("900"))
        self.assertTrue(weeks[1].negative)

    def test_overdue_outflow_lands_in_week_one(self):
        today = date(2026, 10, 8)
        weeks = f.forecast(Decimal("0"), [], [f.DatedAmount(date(2026, 10, 1), Decimal("50"), "old")], today, 2)
        self.assertEqual(weeks[0].outflow, Decimal("50.00"))
        self.assertEqual(weeks[1].outflow, Decimal("0.00"))

    def test_forecast_horizon_limit(self):
        with self.assertRaises(f.FinanceRuleViolation):
            f.forecast(Decimal("0"), [], [], date(2026, 1, 1), 27)

    def test_debt_burden_ratio(self):
        self.assertEqual(f.debt_burden(Decimal("5000"), Decimal("20000")), Decimal("0.2500"))
        self.assertIsNone(f.debt_burden(Decimal("5000"), Decimal("0")))

    def test_alerts_fire_for_each_rule(self):
        alerts = f.risk_alerts(
            cash_available=Decimal("100"),
            overdue_count=2,
            overdue_total=Decimal("900"),
            next_seven_days_outflow=Decimal("300"),
            monthly_income=Decimal("10000"),
            monthly_obligations=Decimal("6000"),
            receivables_overdue_total=Decimal("250"),
            forecast_negative_week=3,
            unreconciled_count=4,
        )
        codes = {a.code for a in alerts}
        self.assertEqual(
            codes,
            {"cash_short_next_7_days", "forecast_negative", "overdue_payments", "debt_burden_high",
             "receivables_overdue", "bank_reconciliation_pending"},
        )

    def test_no_alerts_when_healthy(self):
        alerts = f.risk_alerts(
            cash_available=Decimal("50000"), overdue_count=0, overdue_total=Decimal("0"),
            next_seven_days_outflow=Decimal("1000"), monthly_income=Decimal("100000"),
            monthly_obligations=Decimal("10000"), receivables_overdue_total=Decimal("0"),
            forecast_negative_week=None, unreconciled_count=0,
        )
        self.assertEqual(alerts, [])


if __name__ == "__main__":
    unittest.main()
