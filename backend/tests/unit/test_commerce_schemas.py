"""Request-schema tests for Phase 3: strictness, required fields, and the three-way-match link."""

import datetime
import unittest
import uuid

from pydantic import ValidationError

from app.schemas.commerce import DocLineIn, GoodsReceiptIn, ProcessingJobIn, ProductIn, ReceiptLineIn


class CommerceSchemaTests(unittest.TestCase):
    def test_unknown_fields_are_rejected_everywhere(self):
        with self.assertRaises(ValidationError):
            ProductIn(sku="A1", name="Item", unit_of_measure="pcs", is_stock_item=True, unexpected=1)
        with self.assertRaises(ValidationError):
            DocLineIn(quantity="1", stock_override=True)

    def test_bill_line_accepts_goods_receipt_link_for_three_way_match(self):
        link = uuid.uuid4()
        line = DocLineIn(quantity="2", goods_receipt_line_id=str(link))
        self.assertEqual(line.goods_receipt_line_id, link)

    def test_product_requires_sku_name_and_unit(self):
        with self.assertRaises(ValidationError):
            ProductIn(name="Item", unit_of_measure="pcs", is_stock_item=True)
        with self.assertRaises(ValidationError):
            ProductIn(sku="A1", unit_of_measure="pcs", is_stock_item=True)

    def test_quantities_and_money_are_strings_not_floats(self):
        with self.assertRaises(ValidationError):
            ReceiptLineIn(purchase_order_line_id=uuid.uuid4(), quantity=2.5)

    def test_goods_receipt_needs_at_least_one_line(self):
        with self.assertRaises(ValidationError):
            GoodsReceiptIn(
                purchase_order_id=uuid.uuid4(),
                receipt_date=datetime.date(2026, 10, 8),
                warehouse_id=uuid.uuid4(),
                location_id=uuid.uuid4(),
                lines=[],
            )

    def test_processing_job_takes_only_a_document_id(self):
        with self.assertRaises(ValidationError):
            ProcessingJobIn(document_id=str(uuid.uuid4()), owner_id=str(uuid.uuid4()))


if __name__ == "__main__":
    unittest.main()
