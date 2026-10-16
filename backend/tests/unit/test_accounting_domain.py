"""Pure accounting rules: balancing, money precision, normal balances, reports and the AI boundary."""

import unittest
from datetime import date
from decimal import Decimal

from app.accounting.ai_boundary import READ_ONLY_TOOLS, assert_read_only
from app.accounting.default_chart import DEFAULT_CHART
from app.accounting.domain import (
    ACCOUNT_SUBTYPES,
    AccountCategory,
    AccountingRuleViolation,
    AccountTotals,
    LineAmounts,
    Side,
    balance_sheet,
    ledger_with_running_balance,
    money_text,
    natural_balance,
    normal_side,
    parse_money,
    period_contains,
    profit_and_loss,
    reverse_lines,
    trial_balance,
    validate_account_code,
    validate_entry,
    validate_line,
    validate_parent_category,
    validate_period,
    validate_subtype,
)

D = Decimal


def line(account, debit="0", credit="0"):
    return LineAmounts(account, D(debit), D(credit))


def code_of(callable_, *args):
    try:
        callable_(*args)
    except AccountingRuleViolation as error:
        return error.code
    return None


class MoneyParsingTests(unittest.TestCase):
    def test_accepts_decimal_strings_and_ints(self):
        self.assertEqual(parse_money("1250.5"), D("1250.50"))
        self.assertEqual(parse_money("0.01"), D("0.01"))
        self.assertEqual(parse_money(7), D("7.00"))

    def test_rejects_floats_and_booleans(self):
        self.assertEqual(code_of(parse_money, 0.1), "money_type")
        self.assertEqual(code_of(parse_money, True), "money_type")

    def test_rejects_more_than_two_decimal_places_instead_of_rounding(self):
        self.assertEqual(code_of(parse_money, "10.005"), "money_format")

    def test_rejects_non_numeric_text_and_scientific_notation(self):
        for bad in ("abc", "1e3", "", " ", "1,000.00", "--1"):
            self.assertIsNotNone(code_of(parse_money, bad), bad)

    def test_rejects_too_many_integer_digits(self):
        self.assertEqual(code_of(parse_money, "12345678901234567"), "money_format")

    def test_negative_zero_is_normalised(self):
        self.assertEqual(parse_money("-0.00"), D("0.00"))
        self.assertEqual(money_text(parse_money("-0")), "0.00")

    def test_no_binary_float_drift_in_sums(self):
        # 0.1 + 0.2 is not 0.3 in binary floats; with Decimal it must be exact.
        total = parse_money("0.10") + parse_money("0.20")
        self.assertEqual(money_text(total), "0.30")


class LineAndEntryBalancingTests(unittest.TestCase):
    def test_balanced_two_line_entry_is_accepted(self):
        totals = validate_entry([line("cash", "100.00"), line("sales", credit="100.00")])
        self.assertEqual(totals, (D("100.00"), D("100.00")))

    def test_unbalanced_entry_is_rejected(self):
        with self.assertRaises(AccountingRuleViolation) as caught:
            validate_entry([line("cash", "100.00"), line("sales", credit="99.99")])
        self.assertEqual(caught.exception.code, "unbalanced_entry")

    def test_single_line_entry_is_rejected(self):
        self.assertEqual(code_of(validate_entry, [line("cash", "5.00")]), "too_few_lines")

    def test_empty_entry_is_rejected(self):
        self.assertEqual(code_of(validate_entry, []), "too_few_lines")

    def test_negative_debit_is_rejected(self):
        self.assertEqual(code_of(validate_line, D("-1.00"), D("0.00")), "negative_amount")

    def test_negative_credit_is_rejected(self):
        self.assertEqual(code_of(validate_line, D("0.00"), D("-1.00")), "negative_amount")

    def test_line_with_both_debit_and_credit_is_rejected(self):
        self.assertEqual(code_of(validate_line, D("1.00"), D("1.00")), "line_side")

    def test_zero_only_line_is_rejected(self):
        self.assertEqual(code_of(validate_line, D("0.00"), D("0.00")), "line_side")

    def test_multi_line_split_entry_balances(self):
        entry = [line("cash", "60.00"), line("bank", "40.00"), line("sales", credit="100.00")]
        self.assertEqual(validate_entry(entry), (D("100.00"), D("100.00")))

    def test_amount_over_maximum_is_rejected(self):
        too_big = D("10000000000000000.00")
        self.assertEqual(code_of(validate_line, too_big, D("0.00")), "amount_too_large")


class ReversalTests(unittest.TestCase):
    def test_reversal_swaps_sides_and_is_balanced(self):
        original = [line("rent", "500.00"), line("bank", credit="500.00")]
        reversed_ = reverse_lines(original)
        self.assertEqual(reversed_[0].credit, D("500.00"))
        self.assertEqual(reversed_[1].debit, D("500.00"))
        validate_entry(reversed_)

    def test_entry_plus_reversal_nets_every_account_to_zero(self):
        original = [line("rent", "500.00"), line("bank", credit="500.00")]
        combined = original + reverse_lines(original)
        totals = {}
        for item in combined:
            debit, credit = totals.get(item.account_id, (D("0"), D("0")))
            totals[item.account_id] = (debit + item.debit, credit + item.credit)
        for debit, credit in totals.values():
            self.assertEqual(debit, credit)


class HierarchyAndCodeTests(unittest.TestCase):
    def test_account_codes_must_be_digits_of_four_to_ten(self):
        self.assertEqual(validate_account_code(" 1100 "), "1100")
        for bad in ("11", "ABCD", "1100-1", "12345678901"):
            self.assertEqual(code_of(validate_account_code, bad), "account_code_format", bad)

    def test_child_must_match_parent_category(self):
        validate_parent_category(AccountCategory.ASSET, AccountCategory.ASSET)
        self.assertEqual(
            code_of(validate_parent_category, AccountCategory.EXPENSE, AccountCategory.ASSET),
            "parent_category_mismatch",
        )

    def test_unknown_subtype_is_rejected(self):
        self.assertEqual(code_of(validate_subtype, "unknown_kind"), "account_subtype")
        for subtype in ACCOUNT_SUBTYPES:
            validate_subtype(subtype)

    def test_default_chart_is_consistent(self):
        codes = [account.code for account in DEFAULT_CHART]
        self.assertEqual(len(codes), len(set(codes)), "duplicate default codes")
        by_code = {account.code: account for account in DEFAULT_CHART}
        for account in DEFAULT_CHART:
            if account.parent_code is None:
                continue
            parent = by_code[account.parent_code]
            self.assertEqual(parent.category, account.category, account.code)
            self.assertEqual(parent.subtype, "header", account.code)
            validate_account_code(account.code)
            validate_subtype(account.subtype)

    def test_default_chart_has_cash_and_bank_subtypes(self):
        subtypes = {account.subtype for account in DEFAULT_CHART}
        self.assertTrue({"cash", "bank", "petty_cash"}.issubset(subtypes))


class NormalBalanceTests(unittest.TestCase):
    def test_normal_sides(self):
        self.assertIs(normal_side(AccountCategory.ASSET), Side.DEBIT)
        self.assertIs(normal_side(AccountCategory.EXPENSE), Side.DEBIT)
        for category in (AccountCategory.LIABILITY, AccountCategory.EQUITY, AccountCategory.INCOME):
            self.assertIs(normal_side(category), Side.CREDIT)

    def test_natural_balance_respects_category(self):
        self.assertEqual(natural_balance(AccountCategory.ASSET, D("300"), D("120")), D("180"))
        self.assertEqual(natural_balance(AccountCategory.INCOME, D("20"), D("500")), D("480"))
        # Abnormal balance is negative, not hidden.
        self.assertEqual(natural_balance(AccountCategory.ASSET, D("0"), D("50")), D("-50"))


class TrialBalanceTests(unittest.TestCase):
    def sample_totals(self):
        return [
            AccountTotals("1200", "Bank", AccountCategory.ASSET, D("1300.00"), D("0.00")),
            AccountTotals("3000", "Equity", AccountCategory.EQUITY, D("0.00"), D("1000.00")),
            AccountTotals("4100", "Sales", AccountCategory.INCOME, D("0.00"), D("300.00")),
            AccountTotals("5100", "Rent", AccountCategory.EXPENSE, D("0.00"), D("0.00")),
        ]

    def test_trial_balance_totals_match_for_balanced_books(self):
        tb = trial_balance(self.sample_totals())
        self.assertTrue(tb.is_balanced)
        self.assertEqual(tb.total_debit, D("1300.00"))
        self.assertEqual(tb.total_credit, D("1300.00"))

    def test_unused_accounts_are_omitted(self):
        codes = [row.code for row in trial_balance(self.sample_totals()).rows]
        self.assertNotIn("5100", codes)

    def test_rows_are_sorted_by_code(self):
        codes = [row.code for row in trial_balance(reversed(self.sample_totals())).rows]
        self.assertEqual(codes, sorted(codes))

    def test_unbalanced_source_data_is_visible_in_totals(self):
        broken = [AccountTotals("1200", "Bank", AccountCategory.ASSET, D("100.00"), D("0.00"))]
        self.assertFalse(trial_balance(broken).is_balanced)


class ReportTests(unittest.TestCase):
    def books(self):
        return [
            AccountTotals("1200", "Bank", AccountCategory.ASSET, D("900.00"), D("0.00")),
            AccountTotals("2200", "Loans", AccountCategory.LIABILITY, D("0.00"), D("200.00")),
            AccountTotals("3000", "Equity", AccountCategory.EQUITY, D("0.00"), D("500.00")),
            AccountTotals("4100", "Sales", AccountCategory.INCOME, D("0.00"), D("300.00")),
            AccountTotals("5100", "Rent", AccountCategory.EXPENSE, D("100.00"), D("0.00")),
        ]

    def test_profit_and_loss(self):
        pnl = profit_and_loss(self.books())
        self.assertEqual(pnl.income, D("300.00"))
        self.assertEqual(pnl.expenses, D("100.00"))
        self.assertEqual(pnl.net_profit, D("200.00"))

    def test_balance_sheet_equation_holds(self):
        sheet = balance_sheet(self.books())
        self.assertEqual(sheet.assets, D("900.00"))
        self.assertEqual(sheet.liabilities, D("200.00"))
        self.assertEqual(sheet.equity, D("500.00"))
        self.assertEqual(sheet.current_earnings, D("200.00"))
        self.assertTrue(sheet.is_balanced)

    def test_balance_sheet_detects_unbalanced_books(self):
        broken = self.books() + [
            AccountTotals("1300", "AR", AccountCategory.ASSET, D("50.00"), D("0.00"))
        ]
        self.assertFalse(balance_sheet(broken).is_balanced)


class LedgerAndPeriodTests(unittest.TestCase):
    def test_running_balance_respects_category(self):
        movements = [
            (date(2026, 10, 1), 1, "Opening", D("500.00"), D("0.00")),
            (date(2026, 10, 3), 2, "Payment", D("0.00"), D("120.00")),
        ]
        rows = ledger_with_running_balance(AccountCategory.ASSET, movements)
        self.assertEqual([row.balance for row in rows], [D("500.00"), D("380.00")])

    def test_liability_running_balance_grows_on_credit(self):
        movements = [(date(2026, 10, 1), 1, "Loan", D("0.00"), D("1000.00"))]
        rows = ledger_with_running_balance(AccountCategory.LIABILITY, movements)
        self.assertEqual(rows[0].balance, D("1000.00"))

    def test_period_bounds_are_inclusive(self):
        start, end = date(2026, 10, 1), date(2026, 10, 31)
        self.assertTrue(period_contains(start, end, start))
        self.assertTrue(period_contains(start, end, end))
        self.assertFalse(period_contains(start, end, date(2026, 11, 1)))

    def test_inverted_period_is_rejected(self):
        self.assertEqual(code_of(validate_period, date(2026, 10, 2), date(2026, 10, 1)), "invalid_period")


class AiBoundaryTests(unittest.TestCase):
    def test_registered_tools_are_read_only(self):
        assert_read_only(READ_ONLY_TOOLS)

    def test_write_named_tool_is_refused(self):
        with self.assertRaises(ValueError):
            assert_read_only(frozenset({"accounting.post_journal_entry"}))
        with self.assertRaises(ValueError):
            assert_read_only(frozenset({"accounting.run_sql"}))


if __name__ == "__main__":
    unittest.main()
