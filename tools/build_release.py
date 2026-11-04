"""Deterministic builder for the submission archive ``code.zip``.

Packages ``README.md``, ``problem_statement.md``, and the ``code/`` +
``evaluation/`` trees with sorted entries, a fixed timestamp, and a fixed
compression level so repeated builds are byte-identical.  Cache, VCS,
virtualenv, secret, and log files are excluded while collecting, then the
written archive is re-opened and asserted to contain no banned name and
all required entries.  Exits non-zero on any violation.

Run: python tools/build_release.py
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_PATH = REPO_ROOT / "code.zip"

ROOT_FILES = ("README.md", "problem_statement.md")
TREE_DIRS = ("code", "docs", "evaluation", "tools")

# Skipped as normal build noise (caches, VCS, envs, plan scratch).
SKIP_DIR_NAMES = frozenset({"__pycache__", ".pytest_cache", ".venv", ".git", ".superpowers"})
SKIP_SUFFIXES = (".pyc", ".pyo")

# Never allowed inside the archive (AGENTS 6.5: no keys, credentials, config).
BANNED_FILE_NAMES = frozenset({
    "log.txt", ".env", "credentials.json", "id_rsa", "id_ed25519", ".netrc", ".htpasswd",
})
BANNED_SUFFIXES = (".env",)
BANNED_DIR_NAMES = frozenset({"__pycache__", ".pytest_cache", ".venv", ".git", ".superpowers"})

REQUIRED_ENTRIES = ("README.md", "problem_statement.md", "evaluation/usage_report.md")

ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)  # fixed timestamp -> deterministic archive
FILE_MODE = 0o100644 << 16          # regular file, rw-r--r--
COMPRESS_LEVEL = 9


def _is_secret_name(path: Path) -> bool:
    return path.name in BANNED_FILE_NAMES or path.name.endswith(BANNED_SUFFIXES)


def collect_entries() -> list[Path]:
    """Files to package, sorted by their archive name; secrets abort the build."""
    entries: list[Path] = []
    for name in ROOT_FILES:
        path = REPO_ROOT / name
        if not path.is_file():
            raise SystemExit(f"FAIL required root file missing: {name}")
        entries.append(path)
    for tree in TREE_DIRS:
        root = REPO_ROOT / tree
        if not root.is_dir():
            raise SystemExit(f"FAIL required tree missing: {tree}/")
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(REPO_ROOT).parts
            if any(part in SKIP_DIR_NAMES for part in rel_parts) or path.suffix in SKIP_SUFFIXES:
                continue
            if _is_secret_name(path):
                raise SystemExit(f"FAIL secret-like file in package set: {'/'.join(rel_parts)}")
            entries.append(path)
    return sorted(entries, key=lambda p: p.relative_to(REPO_ROOT).as_posix())


def build(entries: list[Path]) -> None:
    with zipfile.ZipFile(ARCHIVE_PATH, "w") as archive:
        for path in entries:
            arcname = path.relative_to(REPO_ROOT).as_posix()
            info = zipfile.ZipInfo(arcname, date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = FILE_MODE
            archive.writestr(
                info,
                path.read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=COMPRESS_LEVEL,
            )


def verify() -> list[str]:
    """Re-open the archive and return violations (empty list == clean)."""
    violations: list[str] = []
    with zipfile.ZipFile(ARCHIVE_PATH) as archive:
        corrupt = archive.testzip()
        if corrupt is not None:
            violations.append(f"corrupt member: {corrupt}")
        names = archive.namelist()
    if len(names) != len(set(names)):
        violations.append("duplicate member names")
    for name in names:
        parts = PurePosixPath(name).parts
        if name.startswith("/") or "\\" in name or ".." in parts or len(parts) < 1:
            violations.append(f"non-relative path: {name}")
            continue
        if any(part in BANNED_DIR_NAMES for part in parts):
            violations.append(f"banned directory: {name}")
        if parts[-1] in BANNED_FILE_NAMES or parts[-1].endswith(BANNED_SUFFIXES):
            violations.append(f"banned file name: {name}")
    for required in REQUIRED_ENTRIES:
        if required not in names:
            violations.append(f"missing required entry: {required}")
    return violations


def main() -> int:
    entries = collect_entries()
    build(entries)
    violations = verify()
    names = sorted(zipfile.ZipFile(ARCHIVE_PATH).namelist())
    digest = hashlib.sha256(ARCHIVE_PATH.read_bytes()).hexdigest()
    size = ARCHIVE_PATH.stat().st_size
    print(f"built {ARCHIVE_PATH.name}: {len(names)} files, {size} bytes")
    print(f"sha256 {digest}")
    print(f"top-level entries: {sorted({n.split('/', 1)[0] for n in names})}")
    print(f"banned names inside archive: {[v for v in violations if 'banned' in v] or 'none'}")
    if violations:
        for violation in violations:
            print(f"FAIL {violation}")
        return 1
    print("PASS archive clean: no banned names, required entries present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
