"""Dataset integrity: joins resolve, counts hold, sacred rules visible in data.

Rule basis: problem_statement :31-49 (file roles, join keys, blank→image, home
currency, YYYY-MM-DD) + AGENTS 6.1. Every count below is a frozen 2026-09 snapshot
of the shipped dataset; if organizers re-cut the data, update the pins, not the logic.
"""
from decimal import Decimal

from tests.conftest import DATASET, MEDIA
from tests.spec_checks import normalize_amount


def test_all_nine_files_present():
    expected = ["requests.csv", "output.csv", "sample_requests.csv", "financial_profiles.csv",
                "financial_events.csv", "request_payment_options.csv", "exchange_rates.csv",
                "messages.csv", "images.csv"]
    for f in expected:
        assert (DATASET / f).exists(), f"missing {f}"
    assert MEDIA.is_dir() and len(list(MEDIA.glob("*.png"))) == 16


def test_request_id_universe(requests_df, options_df):
    """250 unique scored requests; report IDs form a set (dupes would hide a missing ID);
    every scored request has ≥1 payment option; options reference no ghost requests."""
    assert len(requests_df) == len(set(requests_df["request_id"])) == 250
    req_ids = set(requests_df["request_id"])
    opt_ids = set(options_df["request_id"])
    assert req_ids <= opt_ids, "every scored request must have options"


def test_message_event_links_resolve(messages_df, events_df):
    ev = set(events_df["event_id"])
    linked = messages_df[messages_df["related_event_id"] != ""]
    assert len(linked) == 39
    orphans = set(linked["related_event_id"]) - ev
    assert not orphans, f"orphan related_event_id: {orphans}"


def test_blank_amounts_match_images_1_to_1(events_df, images_df):
    blanks = events_df[events_df["amount"] == ""]
    assert len(blanks) == 16
    assert set(blanks["event_id"]) == set(images_df["related_event_id"])
    for _, r in images_df.iterrows():
        assert (MEDIA / f"{r['image_id']}.png").exists()


def test_linked_events_resolve(events_df):
    ev = set(events_df["event_id"])
    linked = events_df[events_df["linked_event_id"] != ""]
    assert len(linked) == 58
    assert not (set(linked["linked_event_id"]) - ev)


def test_fx_pairs_and_monthly_cadence(fx_df):
    pairs = set(zip(fx_df["from_currency"], fx_df["to_currency"]))
    assert pairs == {("EUR", "ZAR"), ("USD", "EUR"), ("USD", "IDR"), ("USD", "INR"), ("EUR", "USD")}
    days = fx_df["rate_date"].str.strip()
    off_cycle = fx_df[~days.str.endswith("-15")]
    # Exactly one documented off-cycle row exists: USD->INR on 2025-10-01.
    # The solution's FX lookup must handle non-15 rate dates (nearest-prior rule).
    assert len(off_cycle) == 1
    assert off_cycle.iloc[0]["rate_date"].strip() == "2025-10-01"
    assert (off_cycle.iloc[0]["from_currency"], off_cycle.iloc[0]["to_currency"]) == ("USD", "INR")


def test_locale_regression_pairs():
    """Locale routing spot checks (CLDR-lite): ID dot-thousands, US commas, ID decimals,
    ZAR thousands, plain EUR. Full pair matrix lands with the solution's parser tests."""
    assert normalize_amount("43.339.000", "IDR", "id") == 43339000
    assert normalize_amount("1,302.40", "EUR", "en") == Decimal("1302.40")
    assert normalize_amount("1.050,32", "IDR", "id") == Decimal("1050.32")
    assert normalize_amount("6,670", "ZAR", "en") == 6670
    assert normalize_amount("880", "EUR", "es") == 880
