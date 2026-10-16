#!/usr/bin/env python3
"""Safely restore the cleaned Logu AI CFO source archive into the repository root."""
from __future__ import annotations

import shutil
import stat
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

ARCHIVE = Path("Logu-AI-CFO-clean-upload.zip")
ROOT = Path.cwd().resolve()
BLOCKED_PARTS = {
    ".git", ".github", "node_modules", ".next", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".venv", "venv", "__pypackages__",
}
BLOCKED_NAMES = {
    "credentials.json", "secrets.json", ".neon_url.txt",
    "id_rsa", "id_ed25519",
}
MAX_UNCOMPRESSED = 50 * 1024 * 1024


def safe_parts(info_name: str) -> tuple[str, ...] | None:
    rel = PurePosixPath(info_name)
    if rel.is_absolute() or not rel.parts or any(p in {"", ".", ".."} for p in rel.parts):
        raise SystemExit(f"Unsafe archive path rejected: {info_name!r}")
    if "\\" in info_name or ":" in rel.parts[0]:
        raise SystemExit(f"Unsafe archive path rejected: {info_name!r}")
    return tuple(rel.parts)


def is_blocked(parts: tuple[str, ...]) -> bool:
    name = parts[-1]
    if any(part in BLOCKED_PARTS for part in parts):
        return True
    if name in BLOCKED_NAMES:
        return True
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    if name.endswith((".pem", ".key", ".pyc", ".pyo")):
        return True
    return False


def main() -> int:
    if not ARCHIVE.is_file():
        print("ERROR: source ZIP is missing.", file=sys.stderr)
        return 2
    try:
        with ZipFile(ARCHIVE) as zf:
            infos = [info for info in zf.infolist() if not info.is_dir()]
            if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED:
                print("ERROR: archive exceeds the 50 MiB uncompressed source limit.", file=sys.stderr)
                return 2

            candidates: list[tuple[object, tuple[str, ...]]] = []
            for info in infos:
                parts = safe_parts(info.filename)
                if parts is None or is_blocked(parts):
                    continue
                if stat.S_ISLNK(info.external_attr >> 16):
                    print(f"Skipping symlink: {info.filename}")
                    continue
                candidates.append((info, parts))

            if not candidates:
                print("ERROR: ZIP contains no eligible files.", file=sys.stderr)
                return 2

            # Find the project root in archives that include an enclosing folder.
            root_markers = (
                ("frontend", "package.json"),
                ("backend", "requirements-dev.txt"),
                ("backend", "requirements.txt"),
                ("docker-compose.yml",),
                ("compose.yml",),
            )
            marker_positions: list[int] = []
            for _, parts in candidates:
                for marker in root_markers:
                    for idx in range(len(parts) - len(marker) + 1):
                        if parts[idx:idx + len(marker)] == marker:
                            marker_positions.append(idx)
                            break
            strip_count = 0
            if marker_positions:
                strip_count = min(marker_positions)
            else:
                # Fallback only when all files share one explicit wrapper directory.
                first_parts = {parts[0] for _, parts in candidates}
                if len(first_parts) == 1 and all(len(parts) > 1 for _, parts in candidates):
                    strip_count = 1

            file_names = [
                "/".join(parts[strip_count:])
                for _, parts in candidates
                if len(parts) > strip_count
            ]
            top_level = Counter(name.split("/", 1)[0] for name in file_names)
            print(f"ZIP members: {len(infos)}; eligible files: {len(candidates)}; root prefix removed: {strip_count}")
            print("Top-level entries after normalization: " + ", ".join(
                f"{name} ({count})" for name, count in top_level.most_common(15)
            ))

            restored = 0
            total_bytes = 0
            for info, parts in candidates:
                if len(parts) <= strip_count:
                    continue
                rel_parts = parts[strip_count:]
                if not rel_parts or is_blocked(rel_parts):
                    continue
                target = (ROOT / Path(*rel_parts)).resolve()
                if ROOT not in target.parents:
                    raise SystemExit(f"Archive path escaped repository root: {info.filename!r}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                restored += 1
                total_bytes += info.file_size

        frontend = ROOT / "frontend" / "package.json"
        backend_candidates = (
            ROOT / "backend" / "requirements-dev.txt",
            ROOT / "backend" / "requirements.txt",
            ROOT / "backend" / "pyproject.toml",
        )
        if not frontend.exists():
            print("ERROR: frontend/package.json not found after import.", file=sys.stderr)
            return 3
        if not any(p.exists() for p in backend_candidates):
            print("ERROR: backend dependency manifest not found after import.", file=sys.stderr)
            return 3

        print(f"SUCCESS: restored {restored} files ({total_bytes:,} bytes).")
        return 0
    except BadZipFile:
        print("ERROR: uploaded source ZIP is not a valid ZIP archive.", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
