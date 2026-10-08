"""P1 FX resolution (TDD): exact rate -> nearest-prior + FX_UNKNOWN -> missing, never invented."""
from decimal import Decimal

from code.fx import resolve_rate


def _last_row(fx_df, i):
    return fx_df.iloc[i].to_dict()


def test_exact_date_uses_snapshot_rate(fx_df):
    r = resolve_rate(fx_df.to_dict("records"), "USD", "EUR", "2023-10-15")
    assert r["method"] == "exact"
    assert r["flag"] is None
    assert r["rate"] == Decimal("0.92")


def test_same_month_gap_uses_nearest_prior(fx_df):
    r = resolve_rate(fx_df.to_dict("records"), "USD", "EUR", "2023-10-20")
    assert r["method"] == "prior"
    assert r["flag"] == "FX_UNKNOWN"
    assert r["rate"] == Decimal("0.92")


def test_cross_month_prior_wins_over_none_this_month(fx_df):
    r = resolve_rate(fx_df.to_dict("records"), "USD", "EUR", "2023-11-10")
    assert r["method"] == "prior"
    assert r["flag"] == "FX_UNKNOWN"
    assert r["rate"] == Decimal("0.92")


def test_missing_pair_returns_none_not_invented(fx_df):
    r = resolve_rate(fx_df.to_dict("records"), "EUR", "IDR", "2023-10-15")
    assert r["rate"] is None
    assert r["flag"] == "FX_UNKNOWN"


def test_date_before_any_coverage_returns_none(fx_df):
    r = resolve_rate(fx_df.to_dict("records"), "EUR", "USD", "2024-03-15")
    assert r["rate"] is None
    assert r["flag"] == "FX_UNKNOWN"


def test_after_last_snapshot_stays_on_last_prior(fx_df):
    r = resolve_rate(fx_df.to_dict("records"), "USD", "EUR", "2026-09-20")
    assert r["method"] == "prior"
    assert r["flag"] == "FX_UNKNOWN"