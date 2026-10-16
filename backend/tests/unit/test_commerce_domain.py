"""Pure commerce rules: parsing, arithmetic, valuation, stock floors, quantity and status rules."""

from __future__ import annotations

import unittest
from decimal import Decimal

from app.commerce import domain as d


def D(value: str) -> Decimal:
    return Decimal(value)


class ParsingTests(unittest.TestCase):
    def test_quantity_accepts_up_to_three_decimals(self) -> None:
        self.assertEqual(d.parse_quantity("2.125"), D("2.125"))

    def test_quantity_rejects_zero_negative_float_and_extra_precision(self) -> None:
        for bad in ("0", "-1", "1.0001", "abc", "", "1e3", 2.5, None):
            with self.subTest(value=bad), self.assertRaises(d.CommerceRuleViolation) as ctx:
                d.parse_quantity(bad)  # type: ignore[arg-type]
            self.assertEqual(ctx.exception.code, "invalid_quantity")

    def test_rate_allows_zero_but_not_negative(self) -> None:
        self.assertEqual(d.parse_rate("0"), D("0"))
        with self.assertRaises(d.CommerceRuleViolation):
            d.parse_rate("-0.01")

    def test_amount_rejects_three_decimals(self) -> None:
        with self.assertRaises(d.CommerceRuleViolation):
            d.parse_amount("10.005")

    def test_percent_bounds(self) -> None:
        self.assertEqual(d.parse_percent(""), D("0"))
        self.assertEqual(d.parse_percent("18"), D("18"))
        for bad in ("101", "-1", "12.345"):
            with self.subTest(value=bad), self.assertRaises(d.CommerceRuleViolation):
                d.parse_percent(bad)

    def test_sku_and_barcode_rules(self) -> None:
        self.assertEqual(d.validate_sku(" ABC-01/x "), "ABC-01/x")
        for bad in ("", "-ABC", "SKU WITH SPACE", "x" * 61):
            with self.subTest(sku=bad), self.assertRaises(d.CommerceRuleViolation):
                d.validate_sku(bad)
        self.assertIsNone(d.validate_barcode("  "))
        self.assertEqual(d.validate_barcode("8901234567890"), "8901234567890")
        with self.assertRaises(d.CommerceRuleViolation):
            d.validate_barcode("12")

    def test_hsn_sac_and_units(self) -> None:
        self.assertEqual(d.validate_hsn_sac("8471"), "8471")
        with self.assertRaises(d.CommerceRuleViolation):
            d.validate_hsn_sac("84.71")
        with self.assertRaises(d.CommerceRuleViolation):
            d.validate_uom("furlong")

    def test_date_format(self) -> None:
        self.assertEqual(d.parse_iso_date("2026-10-08", "date"), "2026-10-08")
        for bad in ("08/10/2026", "2026-10-8", None):
            with self.subTest(value=bad), self.assertRaises(d.CommerceRuleViolation):
                d.parse_iso_date(bad, "date")  # type: ignore[arg-type]


class LineTotalTests(unittest.TestCase):
    def test_line_with_discount_and_tax(self) -> None:
        line = d.line_amounts(D("3"), D("100.00"), D("10"), D("18"))
        self.assertEqual(line.subtotal, D("300.00"))
        self.assertEqual(line.discount, D("30.00"))
        self.assertEqual(line.taxable, D("270.00"))
        self.assertEqual(line.tax, D("48.60"))
        self.assertEqual(line.total, D("318.60"))

    def test_rounding_is_half_up_per_line(self) -> None:
        line = d.line_amounts(D("1"), D("0.125"), D("0"), D("0"))
        self.assertEqual(line.subtotal, D("0.13"))

    def test_document_totals_sum_lines(self) -> None:
        lines = [
            d.line_amounts(D("1"), D("100"), D("0"), D("5")),
            d.line_amounts(D("2"), D("50"), D("10"), D("12")),
        ]
        totals = d.document_totals(lines)
        self.assertEqual(totals.subtotal, D("200.00"))
        self.assertEqual(totals.discount, D("10.00"))
        self.assertEqual(totals.taxable, D("190.00"))
        self.assertEqual(totals.tax, D("5.00") + D("10.80"))
        self.assertEqual(totals.grand_total, totals.taxable + totals.tax)

    def test_money_text_is_fixed_precision(self) -> None:
        self.assertEqual(d.money(D("5")), "5.00")
        self.assertEqual(d.cost_text(D("2.5")), "2.5000")


class ValuationTests(unittest.TestCase):
    def _change(self, **kwargs):  # type: ignore[no-untyped-def]
        base = dict(
            balance_quantity=D("0"),
            average_cost=D("0"),
            quantity_delta=D("10"),
            movement_type=d.MovementType.STOCK_IN,
            unit_cost=D("100"),
            allow_negative=False,
        )
        base.update(kwargs)
        return d.apply_stock_change(**base)

    def test_first_receipt_sets_average(self) -> None:
        decision = self._change()
        self.assertEqual(decision.new_quantity, D("10.000"))
        self.assertEqual(decision.new_average_cost, D("100.0000"))

    def test_weighted_average_on_second_receipt(self) -> None:
        decision = self._change(
            balance_quantity=D("10"), average_cost=D("100"), quantity_delta=D("10"), unit_cost=D("200")
        )
        self.assertEqual(decision.new_average_cost, D("150.0000"))
        self.assertEqual(decision.new_quantity, D("20.000"))

    def test_outbound_uses_current_average_and_keeps_it(self) -> None:
        decision = self._change(
            balance_quantity=D("10"),
            average_cost=D("150"),
            quantity_delta=D("-4"),
            movement_type=d.MovementType.SALE_OUT,
            unit_cost=None,
        )
        self.assertEqual(decision.applied_unit_cost, D("150.0000"))
        self.assertEqual(decision.new_average_cost, D("150.0000"))
        self.assertEqual(decision.new_quantity, D("6.000"))

    def test_negative_stock_refused_by_default(self) -> None:
        with self.assertRaises(d.CommerceRuleViolation) as ctx:
            self._change(
                balance_quantity=D("2"),
                average_cost=D("10"),
                quantity_delta=D("-3"),
                movement_type=d.MovementType.STOCK_OUT,
                unit_cost=None,
            )
        self.assertEqual(ctx.exception.code, "negative_stock")

    def test_negative_stock_allowed_only_when_configured(self) -> None:
        decision = self._change(
            balance_quantity=D("2"),
            average_cost=D("10"),
            quantity_delta=D("-3"),
            movement_type=d.MovementType.STOCK_OUT,
            unit_cost=None,
            allow_negative=True,
        )
        self.assertEqual(decision.new_quantity, D("-1.000"))

    def test_direction_must_match_type(self) -> None:
        with self.assertRaises(d.CommerceRuleViolation) as ctx:
            self._change(quantity_delta=D("-1"), movement_type=d.MovementType.STOCK_IN)
        self.assertEqual(ctx.exception.code, "invalid_direction")

    def test_inbound_needs_cost(self) -> None:
        with self.assertRaises(d.CommerceRuleViolation) as ctx:
            self._change(unit_cost=None)
        self.assertEqual(ctx.exception.code, "unit_cost_required")

    def test_zero_movement_refused(self) -> None:
        with self.assertRaises(d.CommerceRuleViolation):
            self._change(quantity_delta=D("0"))


class TransferAndReconciliationTests(unittest.TestCase):
    def test_transfer_requires_two_locations_and_equal_quantity(self) -> None:
        d.check_transfer(from_location="a", to_location="b", quantity_out=D("5"), quantity_in=D("5"))
        with self.assertRaises(d.CommerceRuleViolation) as same:
            d.check_transfer(from_location="a", to_location="a", quantity_out=D("5"), quantity_in=D("5"))
        self.assertEqual(same.exception.code, "same_location")
        with self.assertRaises(d.CommerceRuleViolation) as unbalanced:
            d.check_transfer(from_location="a", to_location="b", quantity_out=D("5"), quantity_in=D("4"))
        self.assertEqual(unbalanced.exception.code, "transfer_unbalanced")

    def test_reconciliation_delta(self) -> None:
        self.assertEqual(d.reconciliation_delta(D("10"), D("7.5")), D("-2.500"))
        self.assertEqual(d.reconciliation_delta(D("10"), D("10")), D("0.000"))
        with self.assertRaises(d.CommerceRuleViolation):
            d.reconciliation_delta(D("10"), D("-1"))

    def test_movement_type_direction_sets_are_disjoint_and_complete(self) -> None:
        self.assertEqual(d.IN_TYPES | d.OUT_TYPES, frozenset(d.MovementType))
        self.assertFalse(d.IN_TYPES & d.OUT_TYPES)


class QuantityRuleTests(unittest.TestCase):
    def test_over_receipt_refused_unless_allowed(self) -> None:
        with self.assertRaises(d.CommerceRuleViolation) as ctx:
            d.check_receipt_quantity(ordered=D("10"), received_before=D("8"), new_quantity=D("3"), allow_over=False)
        self.assertEqual(ctx.exception.code, "over_receipt")
        d.check_receipt_quantity(ordered=D("10"), received_before=D("8"), new_quantity=D("3"), allow_over=True)

    def test_partial_receipt_then_remainder(self) -> None:
        d.check_receipt_quantity(ordered=D("10"), received_before=D("0"), new_quantity=D("4"), allow_over=False)
        d.check_receipt_quantity(ordered=D("10"), received_before=D("4"), new_quantity=D("6"), allow_over=False)
        self.assertEqual(d.po_status_after_receipt(D("10"), D("4")), "partially_received")
        self.assertEqual(d.po_status_after_receipt(D("10"), D("10")), "received")
        self.assertEqual(d.po_status_after_receipt(D("10"), D("0")), "approved")

    def test_bill_cannot_exceed_received(self) -> None:
        with self.assertRaises(d.CommerceRuleViolation) as ctx:
            d.check_bill_quantity(received=D("4"), billed_before=D("3"), new_quantity=D("2"))
        self.assertEqual(ctx.exception.code, "bill_exceeds_receipt")

    def test_invoice_quantity_rules(self) -> None:
        with self.assertRaises(d.CommerceRuleViolation) as over:
            d.check_invoice_quantity(
                ordered=D("5"), invoiced_before=D("3"), new_quantity=D("3"), allow_over=False, allow_partial=True
            )
        self.assertEqual(over.exception.code, "over_invoice")
        with self.assertRaises(d.CommerceRuleViolation) as partial:
            d.check_invoice_quantity(
                ordered=D("5"), invoiced_before=D("0"), new_quantity=D("2"), allow_over=False, allow_partial=False
            )
        self.assertEqual(partial.exception.code, "partial_invoice_disabled")
        self.assertEqual(d.so_status_after_invoicing(D("5"), D("2")), "partially_invoiced")
        self.assertEqual(d.so_status_after_invoicing(D("5"), D("5")), "invoiced")


class TransitionTests(unittest.TestCase):
    def test_allowed_and_refused_transitions(self) -> None:
        d.require_transition("purchase_order", "draft", "approved")
        d.require_transition("sales_invoice", "posted", "voided")
        for kind, current, target in (
            ("purchase_bill", "posted", "draft"),
            ("sales_invoice", "voided", "posted"),
            ("quotation", "draft", "converted"),
            ("purchase_order", "received", "cancelled"),
        ):
            with self.subTest(kind=kind, current=current, target=target), self.assertRaises(
                d.CommerceRuleViolation
            ) as ctx:
                d.require_transition(kind, current, target)
            self.assertEqual(ctx.exception.code, "invalid_transition")

    def test_missing_roles(self) -> None:
        configured = {"inventory": "x", "accounts_payable": "y"}
        self.assertEqual(d.missing_roles(d.REQUIRED_ROLES_FOR_PURCHASE_BILL, configured), ["input_tax"])

    def test_party_roles(self) -> None:
        self.assertTrue(d.party_allowed("both", "supplier"))
        self.assertTrue(d.party_allowed("customer", "customer"))
        self.assertFalse(d.party_allowed("customer", "supplier"))


if __name__ == "__main__":
    unittest.main()
