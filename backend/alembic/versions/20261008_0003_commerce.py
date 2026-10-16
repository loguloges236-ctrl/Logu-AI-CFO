"""Phase 3 commerce schema: products, inventory, purchasing, sales and document processing.

Revision ID: 20261008_0003
Revises: 20261008_0002
Create Date: 2026-10-08

Every statement is explicit SQL. Nothing here uses create_all, drop_all or destructive DDL.
Literal enum lists are part of the schema and must stay equal to app/commerce/domain.py and
app/commerce/extraction.py; tests/unit/test_commerce_static.py checks that.

Database rules added here (in addition to CHECK constraints and foreign keys):

- stock_movements is append-only (UPDATE, DELETE and TRUNCATE are refused);
- a stock movement's sign must match its type, and a transfer's two legs must balance at commit;
- stock_balances cannot go negative unless the warehouse explicitly allows it;
- draft-only document headers and lines are frozen once a document leaves draft; posted and
  voided purchase bills and sales invoices keep their figures (only status and reference fields move);
- goods receipts are append-only;
- sales order lines may change only their invoiced quantity after confirmation.

Accounting integration is done by the application through the Phase 2 accounting service; the database
does not post journal entries. Journal entries remain protected by the Phase 2 triggers.
"""

from alembic import op

revision = "20261008_0003"
down_revision = "20261008_0002"
branch_labels = None
depends_on = None

MOVEMENT_IN = ("stock_in", "grn_in", "return_in", "transfer_in", "adjustment_in")
MOVEMENT_OUT = ("stock_out", "sale_out", "damage_out", "return_out", "transfer_out", "adjustment_out")
MOVEMENT_TYPES = MOVEMENT_IN + MOVEMENT_OUT
UOMS = ("pcs", "kg", "g", "l", "ml", "m", "box", "pack", "pair", "set", "hr")
PARTY_KINDS = ("supplier", "customer", "both")
ACCOUNT_ROLES = (
    "inventory",
    "accounts_receivable",
    "input_tax",
    "accounts_payable",
    "output_tax",
    "sales_revenue",
    "cost_of_goods_sold",
    "default_expense",
)
PR_STATUSES = ("draft", "approved", "rejected", "ordered")
PO_STATUSES = ("draft", "approved", "partially_received", "received", "cancelled")
BILL_STATUSES = ("draft", "posted", "voided")
QUOTE_STATUSES = ("draft", "sent", "accepted", "rejected", "expired", "converted")
SO_STATUSES = ("draft", "confirmed", "partially_invoiced", "invoiced", "cancelled")
INVOICE_STATUSES = ("draft", "posted", "voided")
JOB_STATUSES = ("queued", "processing", "needs_review", "ready", "approved", "rejected", "failed")
DOC_TYPES = (
    "purchase_bill",
    "purchase_order",
    "quotation",
    "sales_order",
    "sales_invoice",
    "goods_receipt",
    "receipt",
    "other",
)

SKU_RE = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,59}$"
BARCODE_RE = r"^[0-9A-Za-z-]{4,64}$"
HSN_RE = r"^[0-9]{4,8}$"
WAREHOUSE_CODE_RE = r"^[A-Z0-9][A-Z0-9_-]{0,19}$"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _ts() -> str:
    return (
        "created_at timestamptz NOT NULL DEFAULT now(),\n"
        "    updated_at timestamptz NOT NULL DEFAULT now()"
    )


STATEMENTS: tuple[str, ...] = (
    # ------------------------------------------------------------ sequences for document numbers
    "CREATE SEQUENCE commerce_purchase_request_seq",
    "CREATE SEQUENCE commerce_purchase_order_seq",
    "CREATE SEQUENCE commerce_goods_receipt_seq",
    "CREATE SEQUENCE commerce_purchase_bill_seq",
    "CREATE SEQUENCE commerce_quotation_seq",
    "CREATE SEQUENCE commerce_sales_order_seq",
    "CREATE SEQUENCE commerce_sales_invoice_seq",
    # ------------------------------------------------------------ catalogue
    f"""
    CREATE TABLE product_categories (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        name varchar(80) NOT NULL UNIQUE,
        description varchar(500),
        {_ts()},
        CONSTRAINT ck_product_categories_name CHECK (char_length(btrim(name)) > 0)
    )
    """,
    f"""
    CREATE TABLE products (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        sku varchar(60) NOT NULL UNIQUE,
        barcode varchar(64) UNIQUE,
        name varchar(160) NOT NULL,
        description varchar(500),
        category_id uuid REFERENCES product_categories(id) ON DELETE RESTRICT,
        unit_of_measure varchar(10) NOT NULL,
        hsn_sac varchar(8),
        tax_rate numeric(7,4),
        purchase_price numeric(18,2),
        selling_price numeric(18,2),
        is_stock_item boolean NOT NULL DEFAULT true,
        is_active boolean NOT NULL DEFAULT true,
        {_ts()},
        CONSTRAINT ck_products_sku CHECK (sku ~ '{SKU_RE}'),
        CONSTRAINT ck_products_barcode CHECK (barcode IS NULL OR barcode ~ '{BARCODE_RE}'),
        CONSTRAINT ck_products_name CHECK (char_length(btrim(name)) > 0),
        CONSTRAINT ck_products_uom CHECK ({_in("unit_of_measure", UOMS)}),
        CONSTRAINT ck_products_hsn CHECK (hsn_sac IS NULL OR hsn_sac ~ '{HSN_RE}'),
        CONSTRAINT ck_products_tax_rate CHECK (tax_rate IS NULL OR (tax_rate >= 0 AND tax_rate <= 100)),
        CONSTRAINT ck_products_purchase_price CHECK (purchase_price IS NULL OR purchase_price >= 0),
        CONSTRAINT ck_products_selling_price CHECK (selling_price IS NULL OR selling_price >= 0)
    )
    """,
    "CREATE INDEX ix_products_name ON products (name)",
    "CREATE INDEX ix_products_category ON products (category_id)",
    "CREATE INDEX ix_products_active ON products (is_active)",
    f"""
    CREATE TABLE parties (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        kind varchar(10) NOT NULL,
        name varchar(160) NOT NULL,
        tax_id varchar(20),
        email varchar(254),
        phone varchar(30),
        is_active boolean NOT NULL DEFAULT true,
        {_ts()},
        CONSTRAINT ck_parties_kind CHECK ({_in("kind", PARTY_KINDS)}),
        CONSTRAINT ck_parties_name CHECK (char_length(btrim(name)) > 0)
    )
    """,
    "CREATE INDEX ix_parties_name ON parties (name)",
    "CREATE INDEX ix_parties_kind_active ON parties (kind, is_active)",
    # ------------------------------------------------------------ warehouses and locations
    f"""
    CREATE TABLE warehouses (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        code varchar(20) NOT NULL UNIQUE,
        name varchar(120) NOT NULL,
        allow_negative_stock boolean NOT NULL DEFAULT false,
        is_active boolean NOT NULL DEFAULT true,
        {_ts()},
        CONSTRAINT ck_warehouses_code CHECK (code ~ '{WAREHOUSE_CODE_RE}')
    )
    """,
    f"""
    CREATE TABLE stock_locations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        warehouse_id uuid NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,
        code varchar(20) NOT NULL,
        name varchar(120) NOT NULL,
        is_active boolean NOT NULL DEFAULT true,
        {_ts()},
        CONSTRAINT uq_stock_locations_code UNIQUE (warehouse_id, code),
        CONSTRAINT ck_stock_locations_code CHECK (code ~ '{WAREHOUSE_CODE_RE}')
    )
    """,
    "CREATE INDEX ix_stock_locations_warehouse ON stock_locations (warehouse_id)",
    # ------------------------------------------------------------ stock
    """
    CREATE TABLE stock_balances (
        product_id uuid NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
        location_id uuid NOT NULL REFERENCES stock_locations(id) ON DELETE RESTRICT,
        quantity numeric(18,3) NOT NULL DEFAULT 0,
        average_cost numeric(18,4) NOT NULL DEFAULT 0,
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (product_id, location_id),
        CONSTRAINT ck_stock_balances_cost CHECK (average_cost >= 0)
    )
    """,
    "CREATE INDEX ix_stock_balances_location ON stock_balances (location_id)",
    f"""
    CREATE TABLE stock_movements (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
        product_id uuid NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
        location_id uuid NOT NULL REFERENCES stock_locations(id) ON DELETE RESTRICT,
        movement_type varchar(20) NOT NULL,
        quantity_delta numeric(18,3) NOT NULL,
        unit_cost numeric(18,4),
        balance_after numeric(18,3) NOT NULL,
        average_cost_after numeric(18,4) NOT NULL,
        source_type varchar(40) NOT NULL DEFAULT 'manual',
        source_id uuid,
        source_line_id uuid,
        transfer_group_id uuid,
        reason varchar(255),
        created_by_owner_id uuid REFERENCES owners(id) ON DELETE RESTRICT,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_stock_movements_type CHECK ({_in("movement_type", MOVEMENT_TYPES)}),
        CONSTRAINT ck_stock_movements_nonzero CHECK (quantity_delta <> 0),
        CONSTRAINT ck_stock_movements_direction CHECK (
            (movement_type IN {MOVEMENT_IN} AND quantity_delta > 0)
            OR (movement_type IN {MOVEMENT_OUT} AND quantity_delta < 0)
        ),
        CONSTRAINT ck_stock_movements_cost CHECK (unit_cost IS NULL OR unit_cost >= 0),
        CONSTRAINT ck_stock_movements_avg CHECK (average_cost_after >= 0)
    )
    """,
    "CREATE INDEX ix_stock_movements_product_created ON stock_movements (product_id, created_at)",
    "CREATE INDEX ix_stock_movements_location ON stock_movements (location_id)",
    "CREATE INDEX ix_stock_movements_source ON stock_movements (source_type, source_id)",
    "CREATE INDEX ix_stock_movements_transfer ON stock_movements (transfer_group_id)",
    "CREATE INDEX ix_stock_movements_created ON stock_movements (created_at)",
    # ------------------------------------------------------------ accounting roles
    f"""
    CREATE TABLE commerce_account_mappings (
        role varchar(40) PRIMARY KEY,
        account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
        updated_at timestamptz NOT NULL DEFAULT now(),
        updated_by_owner_id uuid REFERENCES owners(id) ON DELETE RESTRICT,
        CONSTRAINT ck_commerce_account_mappings_role CHECK ({_in("role", ACCOUNT_ROLES)})
    )
    """,
    # ------------------------------------------------------------ purchase request
    f"""
    CREATE TABLE purchase_requests (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        number varchar(20) NOT NULL UNIQUE,
        request_date date NOT NULL,
        status varchar(20) NOT NULL DEFAULT 'draft',
        requested_by varchar(120),
        notes varchar(500),
        created_by_owner_id uuid REFERENCES owners(id) ON DELETE RESTRICT,
        {_ts()},
        CONSTRAINT ck_purchase_requests_status CHECK ({_in("status", PR_STATUSES)})
    )
    """,
    "CREATE INDEX ix_purchase_requests_status ON purchase_requests (status)",
    "CREATE INDEX ix_purchase_requests_date ON purchase_requests (request_date)",
    f"""
    CREATE TABLE purchase_request_lines (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        purchase_request_id uuid NOT NULL REFERENCES purchase_requests(id) ON DELETE RESTRICT,
        line_number integer NOT NULL,
        product_id uuid REFERENCES products(id) ON DELETE RESTRICT,
        description varchar(255) NOT NULL,
        quantity numeric(18,3) NOT NULL,
        {_ts()},
        CONSTRAINT uq_purchase_request_lines UNIQUE (purchase_request_id, line_number),
        CONSTRAINT ck_purchase_request_lines_number CHECK (line_number > 0),
        CONSTRAINT ck_purchase_request_lines_qty CHECK (quantity > 0)
    )
    """,
    # ------------------------------------------------------------ purchase order
    f"""
    CREATE TABLE purchase_orders (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        number varchar(20) NOT NULL UNIQUE,
        order_date date NOT NULL,
        expected_date date,
        party_id uuid NOT NULL REFERENCES parties(id) ON DELETE RESTRICT,
        purchase_request_id uuid REFERENCES purchase_requests(id) ON DELETE RESTRICT,
        status varchar(20) NOT NULL DEFAULT 'draft',
        subtotal numeric(18,2) NOT NULL DEFAULT 0,
        discount_total numeric(18,2) NOT NULL DEFAULT 0,
        taxable_total numeric(18,2) NOT NULL DEFAULT 0,
        tax_total numeric(18,2) NOT NULL DEFAULT 0,
        grand_total numeric(18,2) NOT NULL DEFAULT 0,
        notes varchar(500),
        created_by_owner_id uuid REFERENCES owners(id) ON DELETE RESTRICT,
        {_ts()},
        CONSTRAINT ck_purchase_orders_status CHECK ({_in("status", PO_STATUSES)}),
        CONSTRAINT ck_purchase_orders_totals CHECK (grand_total = taxable_total + tax_total AND grand_total >= 0),
        CONSTRAINT ck_purchase_orders_dates CHECK (expected_date IS NULL OR expected_date >= order_date)
    )
    """,
    "CREATE INDEX ix_purchase_orders_party ON purchase_orders (party_id)",
    "CREATE INDEX ix_purchase_orders_status ON purchase_orders (status)",
    "CREATE INDEX ix_purchase_orders_date ON purchase_orders (order_date)",
    f"""
    CREATE TABLE purchase_order_lines (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        purchase_order_id uuid NOT NULL REFERENCES purchase_orders(id) ON DELETE RESTRICT,
        line_number integer NOT NULL,
        product_id uuid REFERENCES products(id) ON DELETE RESTRICT,
        description varchar(255) NOT NULL,
        quantity numeric(18,3) NOT NULL,
        rate numeric(18,4) NOT NULL,
        discount_percent numeric(5,2) NOT NULL DEFAULT 0,
        tax_rate numeric(7,4) NOT NULL DEFAULT 0,
        line_subtotal numeric(18,2) NOT NULL,
        line_discount numeric(18,2) NOT NULL,
        taxable_amount numeric(18,2) NOT NULL,
        tax_amount numeric(18,2) NOT NULL,
        line_total numeric(18,2) NOT NULL,
        {_ts()},
        CONSTRAINT uq_purchase_order_lines UNIQUE (purchase_order_id, line_number),
        CONSTRAINT ck_purchase_order_lines_number CHECK (line_number > 0),
        CONSTRAINT ck_purchase_order_lines_qty CHECK (quantity > 0),
        CONSTRAINT ck_purchase_order_lines_rate CHECK (rate >= 0),
        CONSTRAINT ck_purchase_order_lines_discount CHECK (discount_percent >= 0 AND discount_percent <= 100),
        CONSTRAINT ck_purchase_order_lines_tax CHECK (tax_rate >= 0 AND tax_rate <= 100),
        CONSTRAINT ck_purchase_order_lines_total CHECK (line_total = taxable_amount + tax_amount)
    )
    """,
    "CREATE INDEX ix_purchase_order_lines_product ON purchase_order_lines (product_id)",
    # ------------------------------------------------------------ goods receipt (append-only)
    f"""
    CREATE TABLE goods_receipts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        number varchar(20) NOT NULL UNIQUE,
        receipt_date date NOT NULL,
        purchase_order_id uuid NOT NULL REFERENCES purchase_orders(id) ON DELETE RESTRICT,
        warehouse_id uuid NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,
        location_id uuid NOT NULL REFERENCES stock_locations(id) ON DELETE RESTRICT,
        status varchar(20) NOT NULL DEFAULT 'posted',
        notes varchar(500),
        created_by_owner_id uuid REFERENCES owners(id) ON DELETE RESTRICT,
        {_ts()},
        CONSTRAINT ck_goods_receipts_status CHECK (status = 'posted')
    )
    """,
    "CREATE INDEX ix_goods_receipts_po ON goods_receipts (purchase_order_id)",
    "CREATE INDEX ix_goods_receipts_date ON goods_receipts (receipt_date)",
    "CREATE INDEX ix_goods_receipts_warehouse ON goods_receipts (warehouse_id)",
    f"""
    CREATE TABLE goods_receipt_lines (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        goods_receipt_id uuid NOT NULL REFERENCES goods_receipts(id) ON DELETE RESTRICT,
        line_number integer NOT NULL,
        purchase_order_line_id uuid NOT NULL REFERENCES purchase_order_lines(id) ON DELETE RESTRICT,
        product_id uuid NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
        quantity numeric(18,3) NOT NULL,
        unit_cost numeric(18,4) NOT NULL,
        {_ts()},
        CONSTRAINT uq_goods_receipt_lines UNIQUE (goods_receipt_id, line_number),
        CONSTRAINT ck_goods_receipt_lines_qty CHECK (quantity > 0),
        CONSTRAINT ck_goods_receipt_lines_cost CHECK (unit_cost >= 0)
    )
    """,
    "CREATE INDEX ix_goods_receipt_lines_po_line ON goods_receipt_lines (purchase_order_line_id)",
    "CREATE INDEX ix_goods_receipt_lines_product ON goods_receipt_lines (product_id)",
    # ------------------------------------------------------------ purchase bill
    f"""
    CREATE TABLE purchase_bills (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        number varchar(20) NOT NULL UNIQUE,
        supplier_invoice_number varchar(60) NOT NULL,
        bill_date date NOT NULL,
        due_date date,
        party_id uuid NOT NULL REFERENCES parties(id) ON DELETE RESTRICT,
        purchase_order_id uuid REFERENCES purchase_orders(id) ON DELETE RESTRICT,
        status varchar(20) NOT NULL DEFAULT 'draft',
        subtotal numeric(18,2) NOT NULL DEFAULT 0,
        discount_total numeric(18,2) NOT NULL DEFAULT 0,
        taxable_total numeric(18,2) NOT NULL DEFAULT 0,
        tax_total numeric(18,2) NOT NULL DEFAULT 0,
        grand_total numeric(18,2) NOT NULL DEFAULT 0,
        payment_reference varchar(120),
        payment_date date,
        journal_entry_id uuid REFERENCES journal_entries(id) ON DELETE RESTRICT,
        reversal_entry_id uuid REFERENCES journal_entries(id) ON DELETE RESTRICT,
        voided_at timestamptz,
        notes varchar(500),
        created_by_owner_id uuid REFERENCES owners(id) ON DELETE RESTRICT,
        {_ts()},
        CONSTRAINT ck_purchase_bills_status CHECK ({_in("status", BILL_STATUSES)}),
        CONSTRAINT ck_purchase_bills_totals CHECK (grand_total = taxable_total + tax_total AND grand_total >= 0),
        CONSTRAINT ck_purchase_bills_due CHECK (due_date IS NULL OR due_date >= bill_date),
        CONSTRAINT ck_purchase_bills_posted CHECK (
            status = 'draft' OR journal_entry_id IS NOT NULL
        ),
        CONSTRAINT ck_purchase_bills_voided CHECK (
            (status = 'voided') = (reversal_entry_id IS NOT NULL AND voided_at IS NOT NULL)
        )
    )
    """,
    "CREATE UNIQUE INDEX uq_purchase_bills_supplier_invoice ON purchase_bills (party_id, lower(supplier_invoice_number))",
    "CREATE INDEX ix_purchase_bills_party ON purchase_bills (party_id)",
    "CREATE INDEX ix_purchase_bills_status ON purchase_bills (status)",
    "CREATE INDEX ix_purchase_bills_date ON purchase_bills (bill_date)",
    "CREATE INDEX ix_purchase_bills_po ON purchase_bills (purchase_order_id)",
    f"""
    CREATE TABLE purchase_bill_lines (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        purchase_bill_id uuid NOT NULL REFERENCES purchase_bills(id) ON DELETE RESTRICT,
        line_number integer NOT NULL,
        goods_receipt_line_id uuid REFERENCES goods_receipt_lines(id) ON DELETE RESTRICT,
        product_id uuid REFERENCES products(id) ON DELETE RESTRICT,
        description varchar(255) NOT NULL,
        expense_account_id uuid REFERENCES accounts(id) ON DELETE RESTRICT,
        quantity numeric(18,3) NOT NULL,
        rate numeric(18,4) NOT NULL,
        discount_percent numeric(5,2) NOT NULL DEFAULT 0,
        tax_rate numeric(7,4) NOT NULL DEFAULT 0,
        line_subtotal numeric(18,2) NOT NULL,
        line_discount numeric(18,2) NOT NULL,
        taxable_amount numeric(18,2) NOT NULL,
        tax_amount numeric(18,2) NOT NULL,
        line_total numeric(18,2) NOT NULL,
        {_ts()},
        CONSTRAINT uq_purchase_bill_lines UNIQUE (purchase_bill_id, line_number),
        CONSTRAINT ck_purchase_bill_lines_number CHECK (line_number > 0),
        CONSTRAINT ck_purchase_bill_lines_qty CHECK (quantity > 0),
        CONSTRAINT ck_purchase_bill_lines_rate CHECK (rate >= 0),
        CONSTRAINT ck_purchase_bill_lines_discount CHECK (discount_percent >= 0 AND discount_percent <= 100),
        CONSTRAINT ck_purchase_bill_lines_tax CHECK (tax_rate >= 0 AND tax_rate <= 100),
        CONSTRAINT ck_purchase_bill_lines_total CHECK (line_total = taxable_amount + tax_amount),
        CONSTRAINT ck_purchase_bill_lines_source CHECK (
            (goods_receipt_line_id IS NOT NULL AND product_id IS NOT NULL AND expense_account_id IS NULL)
            OR (goods_receipt_line_id IS NULL AND expense_account_id IS NOT NULL)
        )
    )
    """,
    "CREATE INDEX ix_purchase_bill_lines_grn_line ON purchase_bill_lines (goods_receipt_line_id)",
    # ------------------------------------------------------------ quotation
    f"""
    CREATE TABLE quotations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        number varchar(20) NOT NULL UNIQUE,
        quotation_date date NOT NULL,
        valid_until date,
        party_id uuid NOT NULL REFERENCES parties(id) ON DELETE RESTRICT,
        status varchar(20) NOT NULL DEFAULT 'draft',
        subtotal numeric(18,2) NOT NULL DEFAULT 0,
        discount_total numeric(18,2) NOT NULL DEFAULT 0,
        taxable_total numeric(18,2) NOT NULL DEFAULT 0,
        tax_total numeric(18,2) NOT NULL DEFAULT 0,
        grand_total numeric(18,2) NOT NULL DEFAULT 0,
        notes varchar(500),
        created_by_owner_id uuid REFERENCES owners(id) ON DELETE RESTRICT,
        {_ts()},
        CONSTRAINT ck_quotations_status CHECK ({_in("status", QUOTE_STATUSES)}),
        CONSTRAINT ck_quotations_totals CHECK (grand_total = taxable_total + tax_total AND grand_total >= 0),
        CONSTRAINT ck_quotations_valid CHECK (valid_until IS NULL OR valid_until >= quotation_date)
    )
    """,
    "CREATE INDEX ix_quotations_party ON quotations (party_id)",
    "CREATE INDEX ix_quotations_status ON quotations (status)",
    "CREATE INDEX ix_quotations_date ON quotations (quotation_date)",
    f"""
    CREATE TABLE quotation_lines (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        quotation_id uuid NOT NULL REFERENCES quotations(id) ON DELETE RESTRICT,
        line_number integer NOT NULL,
        product_id uuid REFERENCES products(id) ON DELETE RESTRICT,
        description varchar(255) NOT NULL,
        quantity numeric(18,3) NOT NULL,
        rate numeric(18,4) NOT NULL,
        discount_percent numeric(5,2) NOT NULL DEFAULT 0,
        tax_rate numeric(7,4) NOT NULL DEFAULT 0,
        line_subtotal numeric(18,2) NOT NULL,
        line_discount numeric(18,2) NOT NULL,
        taxable_amount numeric(18,2) NOT NULL,
        tax_amount numeric(18,2) NOT NULL,
        line_total numeric(18,2) NOT NULL,
        {_ts()},
        CONSTRAINT uq_quotation_lines UNIQUE (quotation_id, line_number),
        CONSTRAINT ck_quotation_lines_number CHECK (line_number > 0),
        CONSTRAINT ck_quotation_lines_qty CHECK (quantity > 0),
        CONSTRAINT ck_quotation_lines_rate CHECK (rate >= 0),
        CONSTRAINT ck_quotation_lines_discount CHECK (discount_percent >= 0 AND discount_percent <= 100),
        CONSTRAINT ck_quotation_lines_tax CHECK (tax_rate >= 0 AND tax_rate <= 100),
        CONSTRAINT ck_quotation_lines_total CHECK (line_total = taxable_amount + tax_amount)
    )
    """,
    # ------------------------------------------------------------ sales order
    f"""
    CREATE TABLE sales_orders (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        number varchar(20) NOT NULL UNIQUE,
        order_date date NOT NULL,
        party_id uuid NOT NULL REFERENCES parties(id) ON DELETE RESTRICT,
        quotation_id uuid REFERENCES quotations(id) ON DELETE RESTRICT,
        status varchar(20) NOT NULL DEFAULT 'draft',
        subtotal numeric(18,2) NOT NULL DEFAULT 0,
        discount_total numeric(18,2) NOT NULL DEFAULT 0,
        taxable_total numeric(18,2) NOT NULL DEFAULT 0,
        tax_total numeric(18,2) NOT NULL DEFAULT 0,
        grand_total numeric(18,2) NOT NULL DEFAULT 0,
        notes varchar(500),
        created_by_owner_id uuid REFERENCES owners(id) ON DELETE RESTRICT,
        {_ts()},
        CONSTRAINT ck_sales_orders_status CHECK ({_in("status", SO_STATUSES)}),
        CONSTRAINT ck_sales_orders_totals CHECK (grand_total = taxable_total + tax_total AND grand_total >= 0)
    )
    """,
    "CREATE INDEX ix_sales_orders_party ON sales_orders (party_id)",
    "CREATE INDEX ix_sales_orders_status ON sales_orders (status)",
    "CREATE INDEX ix_sales_orders_date ON sales_orders (order_date)",
    f"""
    CREATE TABLE sales_order_lines (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        sales_order_id uuid NOT NULL REFERENCES sales_orders(id) ON DELETE RESTRICT,
        line_number integer NOT NULL,
        product_id uuid REFERENCES products(id) ON DELETE RESTRICT,
        description varchar(255) NOT NULL,
        quantity numeric(18,3) NOT NULL,
        invoiced_quantity numeric(18,3) NOT NULL DEFAULT 0,
        rate numeric(18,4) NOT NULL,
        discount_percent numeric(5,2) NOT NULL DEFAULT 0,
        tax_rate numeric(7,4) NOT NULL DEFAULT 0,
        line_subtotal numeric(18,2) NOT NULL,
        line_discount numeric(18,2) NOT NULL,
        taxable_amount numeric(18,2) NOT NULL,
        tax_amount numeric(18,2) NOT NULL,
        line_total numeric(18,2) NOT NULL,
        {_ts()},
        CONSTRAINT uq_sales_order_lines UNIQUE (sales_order_id, line_number),
        CONSTRAINT ck_sales_order_lines_number CHECK (line_number > 0),
        CONSTRAINT ck_sales_order_lines_qty CHECK (quantity > 0),
        CONSTRAINT ck_sales_order_lines_invoiced CHECK (invoiced_quantity >= 0 AND invoiced_quantity <= quantity),
        CONSTRAINT ck_sales_order_lines_rate CHECK (rate >= 0),
        CONSTRAINT ck_sales_order_lines_discount CHECK (discount_percent >= 0 AND discount_percent <= 100),
        CONSTRAINT ck_sales_order_lines_tax CHECK (tax_rate >= 0 AND tax_rate <= 100),
        CONSTRAINT ck_sales_order_lines_total CHECK (line_total = taxable_amount + tax_amount)
    )
    """,
    "CREATE INDEX ix_sales_order_lines_product ON sales_order_lines (product_id)",
    # ------------------------------------------------------------ sales invoice
    f"""
    CREATE TABLE sales_invoices (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        number varchar(20) NOT NULL UNIQUE,
        invoice_date date NOT NULL,
        due_date date,
        party_id uuid NOT NULL REFERENCES parties(id) ON DELETE RESTRICT,
        sales_order_id uuid NOT NULL REFERENCES sales_orders(id) ON DELETE RESTRICT,
        warehouse_id uuid REFERENCES warehouses(id) ON DELETE RESTRICT,
        location_id uuid REFERENCES stock_locations(id) ON DELETE RESTRICT,
        status varchar(20) NOT NULL DEFAULT 'draft',
        subtotal numeric(18,2) NOT NULL DEFAULT 0,
        discount_total numeric(18,2) NOT NULL DEFAULT 0,
        taxable_total numeric(18,2) NOT NULL DEFAULT 0,
        tax_total numeric(18,2) NOT NULL DEFAULT 0,
        grand_total numeric(18,2) NOT NULL DEFAULT 0,
        cost_of_goods_total numeric(18,2) NOT NULL DEFAULT 0,
        payment_reference varchar(120),
        payment_received_on date,
        journal_entry_id uuid REFERENCES journal_entries(id) ON DELETE RESTRICT,
        reversal_entry_id uuid REFERENCES journal_entries(id) ON DELETE RESTRICT,
        voided_at timestamptz,
        notes varchar(500),
        created_by_owner_id uuid REFERENCES owners(id) ON DELETE RESTRICT,
        {_ts()},
        CONSTRAINT ck_sales_invoices_status CHECK ({_in("status", INVOICE_STATUSES)}),
        CONSTRAINT ck_sales_invoices_totals CHECK (grand_total = taxable_total + tax_total AND grand_total >= 0),
        CONSTRAINT ck_sales_invoices_due CHECK (due_date IS NULL OR due_date >= invoice_date),
        CONSTRAINT ck_sales_invoices_location CHECK ((warehouse_id IS NULL) = (location_id IS NULL)),
        CONSTRAINT ck_sales_invoices_posted CHECK (status = 'draft' OR journal_entry_id IS NOT NULL),
        CONSTRAINT ck_sales_invoices_voided CHECK (
            (status = 'voided') = (reversal_entry_id IS NOT NULL AND voided_at IS NOT NULL)
        )
    )
    """,
    "CREATE INDEX ix_sales_invoices_party ON sales_invoices (party_id)",
    "CREATE INDEX ix_sales_invoices_order ON sales_invoices (sales_order_id)",
    "CREATE INDEX ix_sales_invoices_status ON sales_invoices (status)",
    "CREATE INDEX ix_sales_invoices_date ON sales_invoices (invoice_date)",
    f"""
    CREATE TABLE sales_invoice_lines (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        sales_invoice_id uuid NOT NULL REFERENCES sales_invoices(id) ON DELETE RESTRICT,
        line_number integer NOT NULL,
        sales_order_line_id uuid NOT NULL REFERENCES sales_order_lines(id) ON DELETE RESTRICT,
        product_id uuid REFERENCES products(id) ON DELETE RESTRICT,
        description varchar(255) NOT NULL,
        quantity numeric(18,3) NOT NULL,
        rate numeric(18,4) NOT NULL,
        discount_percent numeric(5,2) NOT NULL DEFAULT 0,
        tax_rate numeric(7,4) NOT NULL DEFAULT 0,
        line_subtotal numeric(18,2) NOT NULL,
        line_discount numeric(18,2) NOT NULL,
        taxable_amount numeric(18,2) NOT NULL,
        tax_amount numeric(18,2) NOT NULL,
        line_total numeric(18,2) NOT NULL,
        unit_cost numeric(18,4),
        cost_amount numeric(18,2),
        {_ts()},
        CONSTRAINT uq_sales_invoice_lines UNIQUE (sales_invoice_id, line_number),
        CONSTRAINT ck_sales_invoice_lines_number CHECK (line_number > 0),
        CONSTRAINT ck_sales_invoice_lines_qty CHECK (quantity > 0),
        CONSTRAINT ck_sales_invoice_lines_rate CHECK (rate >= 0),
        CONSTRAINT ck_sales_invoice_lines_discount CHECK (discount_percent >= 0 AND discount_percent <= 100),
        CONSTRAINT ck_sales_invoice_lines_tax CHECK (tax_rate >= 0 AND tax_rate <= 100),
        CONSTRAINT ck_sales_invoice_lines_total CHECK (line_total = taxable_amount + tax_amount)
    )
    """,
    "CREATE INDEX ix_sales_invoice_lines_order_line ON sales_invoice_lines (sales_order_line_id)",
    # ------------------------------------------------------------ document processing
    f"""
    CREATE TABLE document_processing_jobs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        document_id uuid NOT NULL UNIQUE REFERENCES documents(id) ON DELETE RESTRICT,
        status varchar(20) NOT NULL DEFAULT 'queued',
        classification varchar(30),
        classification_confidence numeric(4,3),
        provider_name varchar(60) NOT NULL,
        provider_available boolean NOT NULL DEFAULT false,
        raw_extraction jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        current_payload jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        field_confidence jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        issues jsonb NOT NULL DEFAULT '[]'::jsonb,
        review_acknowledged boolean NOT NULL DEFAULT false,
        approved_document_type varchar(40),
        approved_document_id uuid,
        created_by_owner_id uuid REFERENCES owners(id) ON DELETE RESTRICT,
        reviewed_at timestamptz,
        decided_at timestamptz,
        {_ts()},
        CONSTRAINT ck_document_jobs_status CHECK ({_in("status", JOB_STATUSES)}),
        CONSTRAINT ck_document_jobs_classification CHECK (classification IS NULL OR {_in("classification", DOC_TYPES)}),
        CONSTRAINT ck_document_jobs_confidence CHECK (
            classification_confidence IS NULL OR (classification_confidence >= 0 AND classification_confidence <= 1)
        ),
        CONSTRAINT ck_document_jobs_approved CHECK (
            status <> 'approved' OR (approved_document_type IS NOT NULL AND approved_document_id IS NOT NULL)
        )
    )
    """,
    "CREATE INDEX ix_document_jobs_status ON document_processing_jobs (status, created_at)",
    # ------------------------------------------------------------ guard functions
    """
    CREATE OR REPLACE FUNCTION logu_reject_change() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION '% on % is not allowed: this history is append-only', TG_OP, TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
    END;
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION logu_check_transfer_group() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        legs integer;
        net numeric;
    BEGIN
        SELECT count(*), COALESCE(sum(quantity_delta), 0) INTO legs, net
        FROM stock_movements WHERE transfer_group_id = NEW.transfer_group_id;
        IF legs <> 2 OR net <> 0 THEN
            RAISE EXCEPTION 'a stock transfer must have exactly two legs that balance (found % legs, net %)',
                legs, net USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END;
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION logu_guard_negative_stock() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        allowed boolean;
    BEGIN
        IF NEW.quantity < 0 THEN
            SELECT w.allow_negative_stock INTO allowed
            FROM stock_locations l JOIN warehouses w ON w.id = l.warehouse_id
            WHERE l.id = NEW.location_id;
            IF NOT COALESCE(allowed, false) THEN
                RAISE EXCEPTION 'negative stock is not allowed in this warehouse'
                    USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION logu_guard_draft_lines() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        parent_table text := TG_ARGV[0];
        parent_column text := TG_ARGV[1];
        parent_id uuid;
        parent_status text;
    BEGIN
        IF TG_OP = 'DELETE' THEN
            parent_id := (to_jsonb(OLD) ->> parent_column)::uuid;
        ELSE
            parent_id := (to_jsonb(NEW) ->> parent_column)::uuid;
        END IF;
        EXECUTE format('SELECT status FROM %I WHERE id = $1', parent_table)
            INTO parent_status USING parent_id;
        IF parent_status IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION 'lines of a % document cannot be changed; only drafts are editable', parent_status
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION logu_guard_sales_order_lines() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        parent_status text;
        parent_id uuid;
    BEGIN
        IF TG_OP = 'UPDATE' AND
           (to_jsonb(OLD) - ARRAY['invoiced_quantity', 'updated_at'])
           = (to_jsonb(NEW) - ARRAY['invoiced_quantity', 'updated_at']) THEN
            RETURN NEW;  -- only the invoiced quantity moved (set by invoice posting)
        END IF;
        IF TG_OP = 'DELETE' THEN
            parent_id := OLD.sales_order_id;
        ELSE
            parent_id := NEW.sales_order_id;
        END IF;
        SELECT status INTO parent_status FROM sales_orders WHERE id = parent_id;
        IF parent_status IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION 'confirmed sales order lines cannot be changed except by invoicing'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION logu_guard_header() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
        allowed_after_draft text[] := TG_ARGV;  -- literal column names given in CREATE TRIGGER
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.status <> 'draft' THEN
                RAISE EXCEPTION '% % cannot be deleted once it has left draft', TG_TABLE_NAME, OLD.status
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN OLD;
        END IF;
        IF OLD.status = 'draft' THEN
            RETURN NEW;
        END IF;
        IF (to_jsonb(OLD) - allowed_after_draft) IS DISTINCT FROM (to_jsonb(NEW) - allowed_after_draft) THEN
            RAISE EXCEPTION 'a % document that is no longer a draft cannot change its figures', OLD.status
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF OLD.status = 'voided' AND NEW.status <> 'voided' THEN
            RAISE EXCEPTION 'a voided document cannot be reopened' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    # ------------------------------------------------------------ triggers
    """
    CREATE TRIGGER stock_movements_append_only
    BEFORE UPDATE OR DELETE ON stock_movements
    FOR EACH ROW EXECUTE FUNCTION logu_reject_change()
    """,
    """
    CREATE TRIGGER stock_movements_no_truncate
    BEFORE TRUNCATE ON stock_movements
    FOR EACH STATEMENT EXECUTE FUNCTION logu_reject_change()
    """,
    """
    CREATE CONSTRAINT TRIGGER stock_movements_transfer_balance
    AFTER INSERT ON stock_movements
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.transfer_group_id IS NOT NULL)
    EXECUTE FUNCTION logu_check_transfer_group()
    """,
    """
    CREATE TRIGGER stock_balances_negative_guard
    BEFORE INSERT OR UPDATE ON stock_balances
    FOR EACH ROW EXECUTE FUNCTION logu_guard_negative_stock()
    """,
    """
    CREATE TRIGGER stock_balances_no_delete
    BEFORE DELETE ON stock_balances
    FOR EACH ROW EXECUTE FUNCTION logu_reject_change()
    """,
    """
    CREATE TRIGGER goods_receipts_append_only
    BEFORE UPDATE OR DELETE ON goods_receipts
    FOR EACH ROW EXECUTE FUNCTION logu_reject_change()
    """,
    """
    CREATE TRIGGER goods_receipt_lines_append_only
    BEFORE UPDATE OR DELETE ON goods_receipt_lines
    FOR EACH ROW EXECUTE FUNCTION logu_reject_change()
    """,
    """
    CREATE TRIGGER purchase_request_lines_draft_only
    BEFORE INSERT OR UPDATE OR DELETE ON purchase_request_lines
    FOR EACH ROW EXECUTE FUNCTION logu_guard_draft_lines('purchase_requests', 'purchase_request_id')
    """,
    """
    CREATE TRIGGER purchase_order_lines_draft_only
    BEFORE INSERT OR UPDATE OR DELETE ON purchase_order_lines
    FOR EACH ROW EXECUTE FUNCTION logu_guard_draft_lines('purchase_orders', 'purchase_order_id')
    """,
    """
    CREATE TRIGGER quotation_lines_draft_only
    BEFORE INSERT OR UPDATE OR DELETE ON quotation_lines
    FOR EACH ROW EXECUTE FUNCTION logu_guard_draft_lines('quotations', 'quotation_id')
    """,
    """
    CREATE TRIGGER purchase_bill_lines_draft_only
    BEFORE INSERT OR UPDATE OR DELETE ON purchase_bill_lines
    FOR EACH ROW EXECUTE FUNCTION logu_guard_draft_lines('purchase_bills', 'purchase_bill_id')
    """,
    """
    CREATE TRIGGER sales_invoice_lines_draft_only
    BEFORE INSERT OR UPDATE OR DELETE ON sales_invoice_lines
    FOR EACH ROW EXECUTE FUNCTION logu_guard_draft_lines('sales_invoices', 'sales_invoice_id')
    """,
    """
    CREATE TRIGGER sales_order_lines_guard
    BEFORE INSERT OR UPDATE OR DELETE ON sales_order_lines
    FOR EACH ROW EXECUTE FUNCTION logu_guard_sales_order_lines()
    """,
    """
    CREATE TRIGGER purchase_requests_header_guard
    BEFORE UPDATE OR DELETE ON purchase_requests
    FOR EACH ROW EXECUTE FUNCTION logu_guard_header('status', 'updated_at')
    """,
    """
    CREATE TRIGGER purchase_orders_header_guard
    BEFORE UPDATE OR DELETE ON purchase_orders
    FOR EACH ROW EXECUTE FUNCTION logu_guard_header('status', 'updated_at')
    """,
    """
    CREATE TRIGGER quotations_header_guard
    BEFORE UPDATE OR DELETE ON quotations
    FOR EACH ROW EXECUTE FUNCTION logu_guard_header('status', 'updated_at')
    """,
    """
    CREATE TRIGGER sales_orders_header_guard
    BEFORE UPDATE OR DELETE ON sales_orders
    FOR EACH ROW EXECUTE FUNCTION logu_guard_header('status', 'updated_at')
    """,
    """
    CREATE TRIGGER purchase_bills_header_guard
    BEFORE UPDATE OR DELETE ON purchase_bills
    FOR EACH ROW EXECUTE FUNCTION logu_guard_header(
        'status', 'updated_at', 'payment_reference', 'payment_date',
        'journal_entry_id', 'reversal_entry_id', 'voided_at')
    """,
    """
    CREATE TRIGGER sales_invoices_header_guard
    BEFORE UPDATE OR DELETE ON sales_invoices
    FOR EACH ROW EXECUTE FUNCTION logu_guard_header(
        'status', 'updated_at', 'payment_reference', 'payment_received_on',
        'journal_entry_id', 'reversal_entry_id', 'voided_at')
    """,
    # Accounting role mappings are configuration; they are never deleted, only re-pointed.
    """
    CREATE TRIGGER commerce_account_mappings_no_delete
    BEFORE DELETE ON commerce_account_mappings
    FOR EACH ROW EXECUTE FUNCTION logu_reject_change()
    """,
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    # Destructive rollback is deliberately unsupported: inventory and financial history are never dropped by
    # a migration. Restore from a verified backup instead (docs/DEPLOYMENT.md).
    raise RuntimeError(
        "Downgrading the commerce schema is not supported. Restore from a verified backup instead."
    )
