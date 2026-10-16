"""Operator commands. Run inside the backend container on the host that owns the database.

  python -m app.cli reset-owner-password --email owner@example.com
  python -m app.cli reconcile-storage            # dry run (default)
  python -m app.cli reconcile-storage --apply

Passwords are read with getpass and never accepted as command-line arguments (shell history, process list).
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app.api.deps import get_storage
from app.core.clock import utcnow
from app.core.config import get_settings
from app.db.session import get_session_factory
from app.models import AuditLog, Owner
from app.security.passwords import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, hash_password
from app.services.auth import revoke_sessions
from app.services.reconcile import reconcile_storage


def _reset_owner_password(email: str) -> int:
    session_factory = get_session_factory()
    with session_factory() as db:
        owner = db.scalar(select(Owner).where(Owner.email == email.strip().lower()))
        if owner is None:
            print("No owner matches that email.", file=sys.stderr)
            return 1
        password = getpass.getpass("New password: ")
        confirmation = getpass.getpass("Repeat new password: ")
        if password != confirmation:
            print("Passwords do not match.", file=sys.stderr)
            return 1
        if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
            print(
                f"Password must be {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters.",
                file=sys.stderr,
            )
            return 1
        owner.password_hash = hash_password(password)
        owner.password_changed_at = utcnow()
        owner.is_active = True
        revoked = revoke_sessions(db, owner.id, "operator_password_reset")
        db.add(
            AuditLog(
                owner_id=owner.id,
                action="owner_password_reset_by_operator",
                success=True,
                metadata_json={"revoked_sessions": revoked},
            )
        )
        db.commit()
    print(f"Password updated. {revoked} active session(s) were signed out.")
    return 0


def _reconcile(apply: bool) -> int:
    storage = get_storage()
    with get_session_factory()() as db:
        report = reconcile_storage(db, storage, apply=apply)
    mode = "APPLIED" if apply else "DRY RUN (no changes made)"
    print(f"Storage reconciliation: {mode}")
    print(f"  orphaned blobs: {len(report.orphaned_blobs)}")
    for key in report.orphaned_blobs:
        print(f"    - {key}")
    print(f"  clean files with missing blobs (investigate): {len(report.missing_clean_blobs)}")
    for key in report.missing_clean_blobs:
        print(f"    - {key}")
    print(f"  stuck scans marked FAILED: {len(report.stuck_scans)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    reset = commands.add_parser("reset-owner-password", help="Set a new owner password (server-side recovery).")
    reset.add_argument("--email", required=True)

    reconcile = commands.add_parser("reconcile-storage", help="Find and optionally remove orphaned blobs.")
    reconcile.add_argument("--apply", action="store_true", help="Make changes. Default is a dry run.")

    args = parser.parse_args(argv)
    get_settings()  # fail fast on invalid configuration
    if args.command == "reset-owner-password":
        return _reset_owner_password(args.email)
    return _reconcile(apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
