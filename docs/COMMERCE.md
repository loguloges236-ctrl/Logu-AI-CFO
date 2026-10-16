# Sales, purchase and inventory (Phase 3)

**Status: code written; verification partial.** Phase 3 adds products and SKUs, warehouses and stock, purchase and sales documents, a provider-independent document-processing pipeline, and controlled posting into the Phase 2 ledger. No payment is executed. No document AI or OCR provider is bundled.

## What runs in this environment (VERIFIED)

| Check | Result |
| --- | --- |
| Pure domain and document-extraction rules (`tests/unit/test_commerce_domain.py`, `test_commerce_extraction.py`) | 42 passing |
| Static ORM-to-migration column parity for all 23 tables, enum and unit/party-kind parity with the domain (`test_commerce_static.py`) | 7 passing |
| Request schemas: strictness, required fields, the goods-receipt link (`test_commerce_schemas.py`) | 6 passing |
| Route authentication and CSRF guard over every commerce route (`test_commerce_routes_static.py`) | 5 passing |
| Full unit suite | 193 passing |
| Database rules on PostgreSQL 16.15: append-only history, negative-stock floor, transfer balance, draft-only edits, voiding rules, catalogue uniqueness, and the migration DDL and triggers (`tests/sql/test_commerce_rules.py`) | 39 passing |
| Route module and ORM modules compile, and pass `ruff` (F, E9) | passing |
| Frontend TypeScript syntax for all commerce files and pages | no syntax errors |

## What is not verified (UNVERIFIED)

- **Runtime of the ORM, services and routes.** SQLAlchemy, FastAPI and psycopg could not be installed in the build environment, so the ORM mapping, the service functions against a live session, and the HTTP routes have not been executed.
- **Integration flows** in `backend/tests/integration/test_commerce_flows.py`: PR → PO → partial GRN → bill → accounts, quotation → order → partial invoice → accounts, stock rules at API level, CSRF and authentication. Written, not executed.
- **Alembic** `upgrade head`, `downgrade` refusal and `alembic check`. The migration's SQL ran on PostgreSQL 16 through the rule tests, but the Alembic runner itself was not executed.
- **Frontend type check and build.** `npm ci` needs the lockfile, which is still not committed (see the roadmap). React and Next type packages were not available, so a full `tsc` could not run and no JSX-level check was possible.

## Data model

Migration `20261008_0003` (after `20261008_0002`). Explicit SQL only: no `create_all`, no `drop_all`, no `DROP TABLE`. Downgrade is refused.

Master data: `product_categories`, `products`, `parties`, `warehouses`, `stock_locations`, `commerce_account_mappings`.
Stock: `stock_balances` (per product and location), `stock_movements` (append-only).
Purchasing: `purchase_requests`, `purchase_request_lines`, `purchase_orders`, `purchase_order_lines`, `goods_receipts`, `goods_receipt_lines`, `purchase_bills`, `purchase_bill_lines`.
Sales: `quotations`, `quotation_lines`, `sales_orders`, `sales_order_lines`, `sales_invoices`, `sales_invoice_lines`.
Documents: `document_processing_jobs`.

Document numbers come from `commerce_*_seq` sequences. Money is `NUMERIC(18,2)`, quantities `NUMERIC(18,3)`, rates and costs `NUMERIC(18,4)`. Enum values are literal `CHECK` lists, kept equal to `app/commerce/domain.py` by a static test.

## Business rules

- **Stock.** Weighted-average cost. Every change is a `stock_movements` row linked to its source document. Quantities cannot be negative. Stock cannot go negative unless the warehouse explicitly sets `allow_negative_stock`; this is enforced in the service and again by a database trigger. Inactive products cannot be newly transacted. Locations must belong to the stated warehouse.
- **Transfers** are two legs (`transfer_out`, `transfer_in`) with the same group id; a deferred database check requires the legs to balance at commit.
- **Reconciliation** posts an adjustment movement for the difference against the counted quantity. It never overwrites a balance.
- **Purchase chain.** A purchase request must be approved before an order can be created from it. A goods receipt may be partial but cannot exceed the open order quantity. A bill's stock lines must name a goods-receipt line (three-way match): they are priced from the receipt's unit cost, and their quantity cannot exceed what was received and not yet billed.
- **Sales chain.** A quotation must be accepted before it converts to an order. An invoice may be partial but cannot exceed the open order quantity. Invoicing a stock item issues stock at weighted-average cost in the same transaction.
- **Draft-only editing.** Lines and headers are editable only in draft. After posting, a bill or invoice keeps its figures; only status and reference fields move. Voiding is by reversal, and nothing is deleted.
- **Payment references** are recorded for audit only. Phase 3 does not execute payments.

## Accounting integration

Business events call the Phase 2 accounting service with `commit=False`, so the document change, its stock movements, its accounting entry and its audit rows commit or roll back together. No journal logic is duplicated.

- Purchase bill posts: debit inventory (or the default expense account), debit input tax, credit accounts payable.
- Sales invoice posts: debit accounts receivable, credit sales revenue, credit output tax. Stock lines also post cost of goods sold, debit COGS, credit inventory.
- Voiding a posted bill or invoice reverses its entry through the Phase 2 reversal.

Roles are mapped by the owner through `PUT /commerce/accounting-roles/{role}`. The roles are `inventory`, `accounts_receivable`, `input_tax`, `accounts_payable`, `output_tax`, `sales_revenue`, `cost_of_goods_sold` and `default_expense`. Posting fails with a clear error when a required role is unmapped.

The default chart from Phase 2 has no tax, COGS or default-expense accounts. The owner creates them, then maps them.

## Document processing

Pipeline: upload (Phase 1, scanned and encrypted) → job created for a clean document → extraction through a provider → classification → validation → review → approval → draft.

- The provider is an interface (`DocumentExtractionProvider`). The default is `UnavailableExtractionProvider`, which reports that no provider is connected. It does not invent results.
- Validation is deterministic. A total must equal the sum of its lines exactly, with no tolerance. A field below the confidence threshold (0.80) marks the job for review.
- Corrections are recorded as a revision and re-validated. Approval creates a draft document only. It never changes stock or the ledger, and posting remains a separate, explicit action.
- Approval with unacknowledged warnings is refused.

## AI boundary

The AI layer may classify, extract, suggest parties, products and accounting treatment, and report missing or ambiguous fields. It may not run SQL, write to the database, post entries, or change stock. Everything it produces is a proposal in a job, and every change passes through the same services a person uses.

## API (authenticated; reads need a session, writes also need the CSRF token)

Base path `/api/v1/commerce`.

- Dashboard: `GET /dashboard`
- Catalogue: `GET|POST /categories`; `GET|POST /products`, `GET|PATCH /products/{id}`; `GET|POST /parties`, `PATCH /parties/{id}`
- Stores: `GET|POST /warehouses`, `PATCH /warehouses/{id}`; `GET|POST /locations`, `PATCH /locations/{id}`; `GET|PUT /accounting-roles`
- Stock: `GET /stock/balances`, `GET /stock/valuation`, `GET /stock/movements`, `GET /stock/integrity`; `POST /stock/receive`, `/stock/issue`, `/stock/damage`, `/stock/return-to-supplier`, `/stock/customer-return`, `/stock/adjust`, `/stock/transfer`, `/stock/reconcile`
- Purchasing: `GET|POST /purchase-requests`, `GET /purchase-requests/{id}`, `POST /purchase-requests/{id}/decision`; `GET|POST /purchase-orders`, `GET /purchase-orders/{id}`, `POST /purchase-orders/{id}/status`; `GET|POST /goods-receipts`, `GET /goods-receipts/{id}`; `GET|POST /purchase-bills`, `GET /purchase-bills/{id}`, `POST /purchase-bills/{id}/post|void|payment-reference`
- Sales: `GET|POST /quotations`, `POST /quotations/{id}/status|convert`; `GET|POST /sales-orders`, `POST /sales-orders/{id}/status`; `GET|POST /sales-invoices`, `POST /sales-invoices/{id}/post|void|payment-reference`
- Document processing: `GET|POST /document-jobs`, `GET /document-jobs/{id}`, `POST /document-jobs/{id}/run|approve|reject`, `PATCH /document-jobs/{id}`

Money and quantities are JSON strings. Errors use the standard envelope with stable codes. Conflicts (stock, state, duplicates) return 409, invalid input 422, and missing records 404.

## Screens (mobile-first, under `/commerce`)

Dashboard; products; warehouses and locations; stock with receive, issue, damage and adjust forms; stock movements; purchase documents with a document page for orders (receipts, bill, payment reference) and bills; sales documents with pages for quotations, orders and invoices; document intake with start, review, correction and approval; accounting mapping. Navigation has a "Business" entry.

The primary path is upload → review if needed → approve, but upload itself stays in Documents (Phase 1). Intake starts processing from an existing document.

## Known limitations

- No document AI or OCR is bundled. Automatic extraction is unavailable until a provider is connected; the review screen is for manual entry.
- The purchase-bill screen bills all received lines in full. Billing a subset of receipt lines is possible through the API only.
- Valuation is weighted average only. There is no FIFO or specific identification.
- Tax fields are metadata. There is no GST engine: no place-of-supply logic, no return filing, no tax-rate table.
- Payments are references only.
- Concurrency under load is not tested. Row locks and sequences protect the core paths; this has not been exercised.
- Owner setup does not create tax or COGS accounts automatically.
