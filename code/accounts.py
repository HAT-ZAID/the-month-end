"""Named users, stored in SQLite, so a person can build their position over time.

Nobody enters their whole financial life in one sitting. One JSON file per
person would still mean "finish it before you close the window"; this keeps a
row per fact instead, so the answer improves every time another bill, salary or
purchase is logged.

The storage is deliberately dumb: one table per contract entity, the same column
names as ``dataset/``, everything text. ``code.account`` remains the only place
that knows what a valid account is, so the kernel contract does not shift.

The database lives beside the app's other local state (``data/``, gitignored)
and never carries credentials.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from code.account import (
    ACCOUNT_VERSION,
    EVENT_FIELDS,
    OPTION_FIELDS,
    PROFILE_FIELDS,
    REQUEST_FIELDS,
    blank_account,
)

SCHEMA_VERSION = 1

#: Table name and id prefix for each repeated entity.
_TABLES = {
    "events": (EVENT_FIELDS, "event_id"),
    "requests": (REQUEST_FIELDS, "request_id"),
    "options": (OPTION_FIELDS, "payment_option_id"),
}


def default_db_path(repo_root=None) -> Path:
    """``<repo>/data/accounts.db`` — local state, never committed."""
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[1]
    return root / "data" / "accounts.db"


def _column_list(fields) -> str:
    return ", ".join(f"{name} TEXT" for name in fields)


def connect(path=None, repo_root=None) -> sqlite3.Connection:
    """An open connection with the schema in place."""
    target = Path(path) if path is not None else default_db_path(repo_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    _create_schema(connection)
    return connection


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS meta (
            name TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS profiles (
            user_name TEXT PRIMARY KEY,
            {_column_list(PROFILE_FIELDS)},
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            user_name TEXT NOT NULL,
            {_column_list(EVENT_FIELDS)},
            seq INTEGER
        );
        CREATE TABLE IF NOT EXISTS requests (
            user_name TEXT NOT NULL,
            {_column_list(REQUEST_FIELDS)},
            seq INTEGER
        );
        CREATE TABLE IF NOT EXISTS payment_options (
            user_name TEXT NOT NULL,
            {_column_list(OPTION_FIELDS)},
            seq INTEGER
        );
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO meta (name, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    connection.commit()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalise_name(name) -> str:
    """A user name that is safe to store and compare."""
    text = str(name or "").strip()
    if not text:
        raise ValueError("a user name is required")
    if len(text) > 80:
        raise ValueError("a user name must be under 80 characters")
    return text


def list_users(path=None, repo_root=None) -> list[str]:
    """Every stored user, alphabetically."""
    connection = connect(path, repo_root)
    try:
        rows = connection.execute(
            "SELECT user_name FROM profiles ORDER BY user_name"
        ).fetchall()
    finally:
        connection.close()
    return [row["user_name"] for row in rows]


def has_user(name, path=None, repo_root=None) -> bool:
    connection = connect(path, repo_root)
    try:
        row = connection.execute(
            "SELECT 1 FROM profiles WHERE user_name = ?", (normalise_name(name),)
        ).fetchone()
    finally:
        connection.close()
    return row is not None


def load(name, path=None, repo_root=None) -> dict | None:
    """The person's account in the shape ``code.account`` uses, or ``None``.

    Returns ``None`` rather than an empty account when the name is unknown, so a
    caller can tell "new person" from "new person with nothing in it".
    """
    user_name = normalise_name(name)
    connection = connect(path, repo_root)
    try:
        row = connection.execute(
            "SELECT * FROM profiles WHERE user_name = ?", (user_name,)
        ).fetchone()
        if row is None:
            return None
        account = blank_account(user_id=user_name)
        for field in PROFILE_FIELDS:
            account["profile"][field] = str(row[field] or "")
        account["profile"]["user_id"] = user_name
        for key, (fields, _id_field) in _TABLES.items():
            table = "payment_options" if key == "options" else key
            records = connection.execute(
                f"SELECT * FROM {table} WHERE user_name = ? ORDER BY seq", (user_name,)
            ).fetchall()
            account[key] = [
                {field: str(record[field] or "") for field in fields}
                for record in records
            ]
    finally:
        connection.close()
    return account


def save(account: dict, name=None, path=None, repo_root=None) -> str:
    """Write the whole account under ``name``; returns the stored user name.

    The repeated entities are replaced wholesale. An account is small (dozens of
    rows), and replacing is the only version that cannot leave a half-written
    ledger behind if the process dies mid-write.
    """
    user_name = normalise_name(
        name or account.get("profile", {}).get("user_id") or "me"
    )
    profile = account.get("profile") or {}
    connection = connect(path, repo_root)
    try:
        stamp = _now()
        existing = connection.execute(
            "SELECT created_at FROM profiles WHERE user_name = ?", (user_name,)
        ).fetchone()
        created = existing["created_at"] if existing else stamp
        values = [str(profile.get(field, "") or "") for field in PROFILE_FIELDS]
        connection.execute(
            f"INSERT INTO profiles (user_name, {', '.join(PROFILE_FIELDS)}, "
            f"created_at, updated_at) VALUES ({', '.join(['?'] * (len(PROFILE_FIELDS) + 3))}) "
            f"ON CONFLICT(user_name) DO UPDATE SET "
            + ", ".join(f"{field} = excluded.{field}" for field in PROFILE_FIELDS)
            + ", updated_at = excluded.updated_at",
            [user_name, *values, created, stamp],
        )
        for key, (fields, _id_field) in _TABLES.items():
            table = "payment_options" if key == "options" else key
            connection.execute(f"DELETE FROM {table} WHERE user_name = ?", (user_name,))
            for position, record in enumerate(account.get(key) or []):
                connection.execute(
                    f"INSERT INTO {table} (user_name, {', '.join(fields)}, seq) "
                    f"VALUES ({', '.join(['?'] * (len(fields) + 2))})",
                    [user_name, *[str(record.get(field, "") or "") for field in fields], position],
                )
        connection.commit()
    finally:
        connection.close()
    return user_name


def create(name, home_currency="", path=None, repo_root=None) -> dict:
    """A new, empty account for ``name``, already stored."""
    user_name = normalise_name(name)
    account = blank_account(user_id=user_name, home_currency=home_currency)
    save(account, name=user_name, path=path, repo_root=repo_root)
    return account


def load_or_create(name, home_currency="", path=None, repo_root=None) -> dict:
    """The stored account, or a fresh one if this is a new person."""
    account = load(name, path=path, repo_root=repo_root)
    if account is not None:
        return account
    return create(name, home_currency=home_currency, path=path, repo_root=repo_root)


def delete(name, path=None, repo_root=None) -> bool:
    """Remove a person and everything logged under their name."""
    user_name = normalise_name(name)
    connection = connect(path, repo_root)
    try:
        removed = connection.execute(
            "DELETE FROM profiles WHERE user_name = ?", (user_name,)
        ).rowcount
        for key in _TABLES:
            table = "payment_options" if key == "options" else key
            connection.execute(f"DELETE FROM {table} WHERE user_name = ?", (user_name,))
        connection.commit()
    finally:
        connection.close()
    return bool(removed)
