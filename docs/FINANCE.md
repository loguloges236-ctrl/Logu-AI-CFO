# Finance, bank, reconciliation, settlements and notifications: status

Each row says what the repository contains. VERIFIED means a test ran here. UNVERIFIED means the code exists but needs the
Python packages (SQLAlchemy, FastAPI, Alembic) or a real provider, which this environment cannot install.

## Verified in this environment

- Unit tests: 315 passed (`python3 -m unittest discover -s tests/unit -t .` from `backend/`).
- PostgreSQL 16.15 rule tests: 78 passed (`COMMERCE_SQL_TEST_ADMIN_URI` set). Covers Phase 3, finance, settlements, migrations 0007 and 0008 (settlement request decisions, payment reference idempotency, journal uniqueness).
- Static wiring tests: imports resolve, every route has the owner session or CSRF, bank code calls only read methods, the AI reader implements every allow-listed tool, the migration chain is 0003 → 0004 → 0005 → 0006 → 0007 with one head.

## Migrations (explicit SQL, downgrade refused)

| Revision | Adds |
|---|---|
| 0004 | debts, debt_schedule_items, payment_obligations, payment_records (append-only, cap trigger), bank_accounts, bank_transactions (append-only), reconciliation_suggestions (guarded), notifications (guarded) |
| 0005 | settlements (append-only, per-document cap under row lock, posted-only, direction guard, reversal rules) |
| 0006 | unique index: one journal entry per settlement |
| 0007 | document types `debt`, `recurring_payment`; reconciliation kinds `date_mismatch`, `amount_mismatch`; `source_job_id` on debts and obligations with one-per-job unique indexes |
| 0008 | `settlement_requests` (pending, approved, rejected; decided rows are final by trigger; one settlement per request); unique journal per payment; unique reference per obligation (reversals excluded) |

## Requirement status

| Area | Status | Notes |
|---|---|---|
| Debt and EMI schedule (pure) | VERIFIED | `build_schedule`, `emi_amount`, exact final-row clearance |
| Debt and obligations (service) | UNVERIFIED | `app/services/finance.py`: create debt (schedule rows + EMI obligation), obligations, payments, reversal, progress, due rows, receivables/payables, cash position, forecast, debt strategy, dashboard. Needs SQLAlchemy. |
| Payment obligations and due dates | UNVERIFIED (service), VERIFIED (pure rules) | `occurrences`, `bucket`, `prioritise`; overdue looks back at most 120 days |
| Recurring payments | UNVERIFIED (service), VERIFIED (mapping) | rent, insurance, credit card, utilities etc. via `recurring_payment` documents |
| Settlement records | VERIFIED (DB rules) | 0005 + 0006 tests |
| Settlement → accounting | UNVERIFIED (service) | `app/services/settlements.py`: approval creates a balanced draft via `create_draft`, posts via `post_entry`, links `journal_entry_id`, all in one unit of work. Reversal uses `reverse_entry` (compensating journal). Replay with the same reference returns the original row. Balanced planner VERIFIED. |
| Obligation payment → journal | UNVERIFIED (service and routes), VERIFIED (DB uniqueness) | `record_payment` requires a cash/bank account and a debit account. It creates the draft, posts it and stores `journal_entry_id` in one transaction. A retry with the same reference returns the original payment with no second journal. Reversal posts a mirror entry. |
| Settlement request lifecycle | UNVERIFIED (service and routes), VERIFIED (DB rules) | `settlement_requests`: create pending, approve (posts through the same locked core as direct approval), reject. Approve replays return the same settlement. Direct `POST /finance/settlements` is unchanged. |
| Bank read-only service | UNVERIFIED (service), VERIFIED (boundary) | `app/services/bank.py`: account import, transaction import with dedupe on (account, external_id), reconciliation persisted as suggestions, owner decision. Reconciliation matching logic VERIFIED. |
| Near-miss reconciliation | VERIFIED (pure) | `date_mismatch` (same amount, 4–30 days), `amount_mismatch` (same date, similar text, different amount). Never auto-matched. |
| Reference mismatch | SCHEMA LIMITATION (not implemented) | `bank_transactions` has no reference field, and journal lines have no reference field that a bank line can be matched on. Matching uses date, amount and description tokens only. Adding a reference match needs a new column on both sides and a provider that supplies the reference. |
| Unmatched accounting transactions | UNVERIFIED | Returned in the reconciliation summary as a count; not persisted. |
| Real bank provider | NOT IMPLEMENTED | `UnavailableBankProvider` by default. `FixtureBankProvider` is a test adapter. No live connection exists or is claimed. |
| Finance API | UNVERIFIED (import/route structure VERIFIED) | `app/api/routes/finance.py`, 32 routes, registered in `main.py`. Owner session on every GET, CSRF on every mutation. Runtime behaviour needs FastAPI. |
| Document → finance mapping | VERIFIED (pure), UNVERIFIED (DB hook) | emi, interest, salary, debt, recurring_payment. Approval in `commerce_documents.approve_job` creates the record in the same transaction, sets `approved_document_*`, audits. Low confidence or invalid values are refused. |
| Supplier payable / customer receivable from documents | NOT IMPLEMENTED as new types | Use existing `purchase_bill` and `sales_invoice`; settlement tracks them. |
| Approval service | UNVERIFIED | Document approval is the existing job approval (owner session, CSRF, status lock, idempotent by job status). Settlement approval is a single audited act. |
| Reminder engine | UNVERIFIED (service), VERIFIED (cycle + dedupe) | `app/services/notifications.py`: obligations, supplier payments, overdue customer collections, document review, reconciliation pending, critical risk alerts. In-app channel only. |
| External email/SMS/push | NOT IMPLEMENTED | `NoExternalChannel` reports nothing delivered. Nothing claims external delivery. |
| Financial brain | VERIFIED (pure), UNVERIFIED (DB aggregation) | Cash, receivables, payables, debt, forecast, avalanche and snowball, priority, risk alerts. All Decimal. |
| AI CFO tools | VERIFIED (allow-list, executor, reader structure), UNVERIFIED (DB reads) | `DBCFOReader` implements the 12 allow-listed tools with getters only. No SQL, no writes, no payments, no secrets to the model. LLM provider NOT IMPLEMENTED (answers come from routing and deterministic data). |
| Voice | NOT IMPLEMENTED | No speaker-verification provider exists. Voice never authorises money movement. |
| Frontend (finance screens) | UNVERIFIED (not built or typechecked here) | `frontend/app/(app)/finance/` has 14 routes: dashboard, cash, upcoming, debts, EMI, interest, salaries, recurring, receivables, payables, settlements, bank (read-only), reconciliation, alerts. Shared code is in `components/finance/`. Mobile-first single column, no horizontal page scroll. Finance added to the app shell. `tsc` syntax check passes. Full typecheck and build need `node_modules`, which were not installed. |
| Document-first UX | UNVERIFIED (runtime), VERIFIED (duplicate wiring) | Upload → extraction → validation → duplicate check → approval → result. `IntakeDetailView` now reads the real job fields (`classification`, `issues`, `provider_name`, `review_acknowledged`). Duplicates show a warning panel. Acknowledgement is required only for warning codes. Blocking issues disable approval. Finance approvals link to the matching Finance screen. Not run in a browser here. Real OCR provider NOT IMPLEMENTED. |
| End-to-end flows A–E | UNVERIFIED (written, not run) | `tests/integration/test_finance_flows.py`. A: EMI payment journal, replay, overpayment, reversal. B: payable request, approve, replay, reject-after-approve, reversal. C: receivable request rejected, nothing posted. D: read-only bank import, suggestions need confirmation. E: CSRF and AI read-only boundary. Flow definitions are stated in the file header because the repository had no separate list. Document-upload-to-debt is not covered by an E2E test. |

## Security boundary

- Every finance route requires the owner session. Mutations also require CSRF (`require_csrf`). A static test enforces this.
- The AI endpoint accepts a question string only. It routes to an allow-listed tool. Unknown tools, unknown arguments and SQL-like keys are refused.
- Bank provider interface: read methods only. A test asserts no write-like method exists and the bank service calls only `list_accounts` and `list_transactions`.
- Payments are append-only and capped by the database. Settlements are append-only and capped per document. Corrections are reversals, never deletes.
- Money is `NUMERIC` in the database and `Decimal` in Python. No floats.
- Errors are stable codes. No tokens, passwords, keys or provider credentials appear in responses.

## Known limits

- The dashboard uses the system date (UTC) for "today". Near midnight in Qatar time this can differ by one day.
- Monthly obligations in the dashboard count only `frequency = monthly`; yearly and weekly items are not annualised.
- Debt outstanding is schedule-based (the closing balance of the last installment due on or before today), not a payments-adjusted balance.
- Obligation payments post a journal only when a cash/bank account and a debit account are supplied. Payments recorded before migration 0008 have no journal link.
- Settlement requests are not reminders or notifications. Only the owner can approve or reject them.
