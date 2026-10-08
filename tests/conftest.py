"""Shared pytest fixtures: repo-root resolution (relative, cross-platform) + dataset loading."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET = REPO_ROOT / "dataset"
MEDIA = DATASET / "media" / "images"

OUTPUT_COLS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]

STATUSES = {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
METHODS = {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}


@lru_cache(maxsize=None)
def _csv(name: str) -> pd.DataFrame:
    return pd.read_csv(DATASET / name, dtype=str, keep_default_na=False)


@pytest.fixture(scope="session")
def requests_df() -> pd.DataFrame:
    return _csv("requests.csv")


@pytest.fixture(scope="session")
def profiles_df() -> pd.DataFrame:
    return _csv("financial_profiles.csv")


@pytest.fixture(scope="session")
def events_df() -> pd.DataFrame:
    return _csv("financial_events.csv")


@pytest.fixture(scope="session")
def options_df() -> pd.DataFrame:
    return _csv("request_payment_options.csv")


@pytest.fixture(scope="session")
def messages_df() -> pd.DataFrame:
    return _csv("messages.csv")


@pytest.fixture(scope="session")
def images_df() -> pd.DataFrame:
    return _csv("images.csv")


@pytest.fixture(scope="session")
def fx_df() -> pd.DataFrame:
    return _csv("exchange_rates.csv")


@pytest.fixture(scope="session")
def gold_df() -> pd.DataFrame:
    return _csv("sample_requests.csv")


def _num(s: str):
    s = (s or "").strip()
    if not s:
        return None
    return float(s)


@pytest.fixture(scope="session")
def root_output_df(requests_df):
    """Root output.csv IFF it is a complete prediction file: exact ID-set match with
    requests.csv and every machine column non-blank (explanations required; earliest
    exempt since empty is legal). Anything less (missing, blank template, half-built)
    returns None so gates SKIP loudly instead of failing opaquely."""
    p = REPO_ROOT / "output.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, dtype=str, keep_default_na=False)
    if set(df.get("request_id", [])) != set(requests_df["request_id"]):
        return None
    machine_cols = [c for c in OUTPUT_COLS if c != "earliest_date_for_full_payment"]
    if df.empty or (df[machine_cols].apply(lambda s: s.str.strip()) == "").any().any():
        return None
    return df


@pytest.fixture(scope="session")
def profile_by_user(profiles_df):
    return {r["user_id"]: r.to_dict() for _, r in profiles_df.iterrows()}


@pytest.fixture(scope="session")
def options_by_request(options_df):
    out: dict[str, list[dict]] = {}
    for _, r in options_df.iterrows():
        out.setdefault(r["request_id"], []).append(r.to_dict())
    return out
