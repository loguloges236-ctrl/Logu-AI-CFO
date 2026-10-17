# Staging readiness gates

A gate is passed only with evidence (a CI run, a test output, or a recorded manual check). Use the labels from PROJECT_COMPLETION.md.

| # | Gate | Evidence required | Status |
|---|---|---|---|
| 1 | Unit tests pass | CI `Run backend unit tests` | Implemented and verified (CI run `39024020732`) |
| 2 | PostgreSQL integration tests pass | CI `Run PostgreSQL integration tests` | Implemented and verified (CI run `39024020732`) |
| 3 | Frontend lint, typecheck, production build | CI frontend steps | Implemented and verified (CI run `39024020732`) |
| 4 | Compose stack starts and serves routes | CI `Compose runtime smoke test` | Implemented and verified (CI run `39023269063`) |
| 5 | Runtime database role is least-privilege | SQL tests (11/11), compose wiring | Implemented and verified (SQL level and compose smoke) |
| 6 | Encryption key rotation | Unit tests for cipher | Implemented but partially verified: re-encryption job not implemented |
| 7 | Backup and restore rehearsal | Recorded rehearsal | Not implemented: not rehearsed |
| 8 | CSP without `unsafe-inline` scripts | Browser test | Not implemented: nonce-based policy planned |
| 9 | Frontend end-to-end tests (browser) | Playwright run in CI | Not implemented |
| 10 | Load and recovery test | Recorded load test and recovery drill | Not implemented |
| 11 | Access logging for sensitive reads | Integration test `test_download_returns_original_bytes_as_attachment_and_is_audited` (CI green) | Implemented but partially verified: download is audited; other read paths are not checked for audit coverage |
| 12 | Own-server host prepared (OS, TLS, DNS, backups, firewall) | Host checklist signed off by owner | Blocked by external dependency: server details needed |
| 13 | Deployment | Explicit approval | Requires my explicit approval |

Gates 7 to 10 must pass before any production data is trusted. Gate 13 is last and is not started.
