"""P3 message parser: entry point for deterministic evidence extraction.

``parse_messages(user_id, messages_df)`` keeps its original signature and its
original keys (``salary_updates``, ``event_updates``) so old callers keep
working, and adds the typed Task 2 contract:

``salary_changes``, ``income_facts``, ``expense_facts``, ``flags``, ``warnings``
and ``facts`` (a :class:`code.facts.EvidenceFacts` object).

Extraction rules live in ``code/facts.py``; amendment conflict resolution lives in
``code/reconcile.py``. Both are deterministic and offline.
"""
from __future__ import annotations

import pandas as pd

from code.facts import extract_facts

_BLANK = {"", "nan", "none", "nat", "null"}


def _cell(value) -> str:
    """Normalize a CSV cell (NaN/None/blank) to a stripped string."""
    if value is None:
        return ""
    try:
        if isinstance(value, float) and value != value:  # NaN
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.lower() in _BLANK else text


def _user_messages(user_id: str, messages_df: pd.DataFrame) -> list[dict]:
    if messages_df is None or not hasattr(messages_df, "columns"):
        return []
    if "user_id" not in messages_df.columns:
        return []
    rows = messages_df[messages_df["user_id"] == user_id]
    messages = []
    for row in rows.to_dict("records"):
        messages.append({key: _cell(value) for key, value in row.items()})
    messages.sort(key=lambda item: (item.get("sent_at", ""), item.get("message_id", "")))
    return messages


def parse_messages(user_id: str, messages_df: pd.DataFrame) -> dict:
    """Parse a user's messages into typed evidence facts.

    Returns the legacy keys plus the Task 2 evidence contract. ``event_updates``
    carries message amendment intents keyed by the exact ``related_event_id``;
    every intent has the keys ``related_event_id``, ``message_id``, ``sent_at``,
    ``action``, ``new_status``, ``new_amount``, ``new_currency`` and ``new_date``,
    with ``None`` where the message states nothing. ``code.reconcile.reconcile_events``
    applies them under the AGENTS 6.3 conflict order (this parser never decides
    which event row wins).
    """
    return extract_facts(_user_messages(user_id, messages_df)).to_dict()
