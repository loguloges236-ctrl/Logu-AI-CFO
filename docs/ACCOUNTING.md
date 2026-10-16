# Core accounting (Phase 2)

This is the deterministic double-entry foundation that later modules will post into. It contains no sales, purchase, inventory, EMI, debt, salary, bank-connection or AI-advice logic.

## Rules

- **Double entry.** Every posted entry has total debit equal to total credit, and at least two lines.
- **Exact money.** Amounts are `Decimal` in Python and `NUMERIC(18,2)` in PostgreSQL. Floats are refused at the API boundary (amounts must be JSON strings). Amounts with more than two decimal places are rejected, not rounded.
- **One side per line.** A line has a debit or a credit above zero, never both, never negative, never zero-only.
- **Normal balances.** Asset and expense accounts grow on debit. Liability, equity and income accounts grow on credit. An abnormal balance is shown as a negative figure, not hidden.
- **Drafts vs posted.** A draft can be created unbalanced and edited or deleted. Posting requires a balanced entry, at least two lines, active detail accounts, and an OPEN period covering the posting date.
- **Immutability.** Posted entries and their lines cannot be updated or deleted (database triggers). The only correction is a **reversal**: a new posted entry with mirrored lines, linked to the original. An entry can be reversed once.
- **Periods.** A period is OPEN or CLOSED. Posting needs an OPEN period. Closing is explicit and irreversible. Periods cannot overlap, cannot be deleted, and their dates cannot change. A correction to a closed period is a reversal posted in an open period.
- **Accounts.** Codes are 4–10 digits and unique. Code and category are fixed once created (and for system accounts, always). Accounts cannot be deleted; they can be deactivated. Header accounts (subtype `header`) group detail accounts and cannot receive postings. A child account must have the same category as its parent.
- **Reporting.** Trial balance, ledger, profit and loss, balance sheet and cash position use **posted** lines only, filtered by **posting date**. Reports are computed from journal lines; no balance table is stored.
- **Audit.** Account create/update/activate/deactivate, draft create/update/delete, posting, reversal, period create and close are written to the append-only audit log.
- **Tax.** Lines can carry tax metadata (name, type, rate, amount). The engine stores and validates it; it computes no GST.

## Default chart

`POST /api/v1/accounting/setup` creates a generic template (assets, liabilities, equity, income, expenses, with cash, petty cash and bank accounts). It refuses to run if any account exists. It is not the owner's real chart.

## Entry numbers

Numbers come from the sequence `journal_entry_number_seq` and are assigned only at posting. Failed postings can leave gaps in the sequence.

## API (all under `/api/v1/accounting`, owner session required; mutations need the CSRF token)

| Method | Path | Purpose |
|---|---|---|
| POST | `/setup` | Create the default chart (once) |
| GET | `/accounts` | List accounts (`include_inactive`) |
| POST | `/accounts` | Create account |
| GET | `/accounts/{id}` | Account details |
| PATCH | `/accounts/{id}` | Change name, description, type, active flag |
| GET | `/accounts/{id}/ledger` | Account history with running balance (`start`, `end`) |
| GET | `/journal-entries` | List entries (`status`, `start`, `end`, `limit`, `offset`) |
| POST | `/journal-entries` | Create draft |
| GET | `/journal-entries/{id}` | Entry with lines |
| PUT | `/journal-entries/{id}` | Replace a draft (drafts only) |
| DELETE | `/journal-entries/{id}` | Delete a draft (drafts only) |
| POST | `/journal-entries/{id}/post` | Post a balanced draft |
| POST | `/journal-entries/{id}/reverse` | Post a reversal of a posted entry |
| GET | `/periods` | List periods |
| POST | `/periods` | Create period |
| POST | `/periods/{id}/close` | Close period |
| GET | `/trial-balance` | Trial balance (`start`, `end`) |
| GET | `/reports/profit-and-loss` | Income, expenses, net profit (`start`, `end`) |
| GET | `/reports/balance-sheet` | Balance sheet as of a date, with equation check |
| GET | `/reports/cash-position` | Cash and bank balances as of a date |
| GET | `/dashboard` | Summary counts and integrity flags |

Errors use the standard envelope. Rule failures have stable codes (for example `unbalanced_entry`, `no_open_period`, `not_draft`, `already_reversed`, `header_account`, `period_overlap`, `duplicate_code`). Unknown or cross-owner resources return `not_found`.

## AI boundary

`app/accounting/ai_boundary.py` lists the only tools a future AI feature may call: read-only ledger, report and cash queries. `assert_read_only` refuses any name that implies a write or raw query. The language model has no database access, no SQL and no posting rights. Posting stays in application services.

## Frontend screens

`/accounting` (dashboard), `/accounting/accounts`, `/accounting/accounts/[id]` (details, edit, history), `/accounting/ledger`, `/accounting/journal`, `/accounting/journal/new`, `/accounting/journal/[id]` (post, delete draft, reverse), `/accounting/journal/[id]/edit`, `/accounting/periods`, `/accounting/trial-balance`, `/accounting/profit-and-loss`, `/accounting/balance-sheet`.

## Known limitations

- Posted entries cannot be edited, so a typo must be corrected with a reversal and a new entry.
- Periods cannot be reopened. This is deliberate; a new period or a reversal is the route.
- The parent of an account cannot be changed after creation.
- Drafts may be saved unbalanced; the rule is enforced at posting.
- Reports are for the owner's ledger only; they are not tax filings and not financial advice.
- Verification status: see [ROADMAP.md](ROADMAP.md).
