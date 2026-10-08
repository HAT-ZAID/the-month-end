"""P2 Ranking logic (TDD): 6-rank order tie-breaking.

Order: Completion Date -> Num Changes -> Total Cost -> Start Date -> Num Legs -> Option ID.
"""
from decimal import Decimal
from code.ranking import rank_options


def test_total_cost_primary():
    opts = [
        {"id": "opt1", "total_cost": Decimal("100"), "completion_date": "2026-01-01", "changes": 0, "legs": 1, "start_date": "2026-01-01"},
        {"id": "opt2", "total_cost": Decimal("90"), "completion_date": "2026-01-01", "changes": 0, "legs": 1, "start_date": "2026-01-01"},
    ]
    # opt2 is cheaper
    assert rank_options(opts)[0]["id"] == "opt2"


def test_completion_date_tiebreaker():
    opts = [
        {"id": "opt1", "total_cost": Decimal("100"), "completion_date": "2026-01-10", "changes": 0, "legs": 1, "start_date": "2026-01-01"},
        {"id": "opt2", "total_cost": Decimal("100"), "completion_date": "2026-01-05", "changes": 0, "legs": 1, "start_date": "2026-01-01"},
    ]
    # opt2 is earlier
    assert rank_options(opts)[0]["id"] == "opt2"


def test_num_changes_tiebreaker():
    opts = [
        {"id": "opt1", "total_cost": Decimal("100"), "completion_date": "2026-01-01", "changes": 2, "legs": 1, "start_date": "2026-01-01"},
        {"id": "opt2", "total_cost": Decimal("100"), "completion_date": "2026-01-01", "changes": 1, "legs": 1, "start_date": "2026-01-01"},
    ]
    # opt2 has fewer changes
    assert rank_options(opts)[0]["id"] == "opt2"


def test_num_legs_tiebreaker():
    opts = [
        {"id": "opt1", "total_cost": Decimal("100"), "completion_date": "2026-01-01", "changes": 0, "legs": 3, "start_date": "2026-01-01"},
        {"id": "opt2", "total_cost": Decimal("100"), "completion_date": "2026-01-01", "changes": 0, "legs": 1, "start_date": "2026-01-01"},
    ]
    # opt2 has fewer legs
    assert rank_options(opts)[0]["id"] == "opt2"


def test_start_date_tiebreaker():
    opts = [
        {"id": "opt1", "total_cost": Decimal("100"), "completion_date": "2026-01-01", "changes": 0, "legs": 1, "start_date": "2026-01-05"},
        {"id": "opt2", "total_cost": Decimal("100"), "completion_date": "2026-01-01", "changes": 0, "legs": 1, "start_date": "2026-01-01"},
    ]
    # opt2 starts earlier
    assert rank_options(opts)[0]["id"] == "opt2"


def test_option_id_final_tiebreaker():
    opts = [
        {"id": "opt_B", "total_cost": Decimal("100"), "completion_date": "2026-01-01", "changes": 0, "legs": 1, "start_date": "2026-01-01"},
        {"id": "opt_A", "total_cost": Decimal("100"), "completion_date": "2026-01-01", "changes": 0, "legs": 1, "start_date": "2026-01-01"},
    ]
    # opt_A wins tie-break (alphabetical/numerical ID)
    assert rank_options(opts)[0]["id"] == "opt_A"


def test_deadline_beats_total_cost():
    # Spec Choosing #1 > #3: on-time expensive beats late cheap.
    opts = [
        {"id": "opt_cheap_late", "total_cost": Decimal("90"), "completion_date": "2026-02-01", "changes": 0, "legs": 1, "start_date": "2026-01-01"},
        {"id": "opt_expensive_ontime", "total_cost": Decimal("100"), "completion_date": "2026-01-05", "changes": 0, "legs": 1, "start_date": "2026-01-01"},
    ]
    assert rank_options(opts)[0]["id"] == "opt_expensive_ontime"


def test_no_changes_beats_total_cost():
    # Spec Choosing #2 > #3: no-changes expensive beats changed cheap.
    opts = [
        {"id": "opt_changed_cheap", "total_cost": Decimal("90"), "completion_date": "2026-01-05", "changes": 1, "legs": 1, "start_date": "2026-01-01"},
        {"id": "opt_clean_pricey", "total_cost": Decimal("100"), "completion_date": "2026-01-05", "changes": 0, "legs": 1, "start_date": "2026-01-01"},
    ]
    assert rank_options(opts)[0]["id"] == "opt_clean_pricey"


def test_start_date_beats_num_legs():
    # Spec Choosing #4 > #5: earlier start beats fewer legs.
    opts = [
        {"id": "opt_early_two", "total_cost": Decimal("100"), "completion_date": "2026-01-05", "changes": 0, "legs": 2, "start_date": "2026-01-01"},
        {"id": "opt_late_one", "total_cost": Decimal("100"), "completion_date": "2026-01-05", "changes": 0, "legs": 1, "start_date": "2026-01-05"},
    ]
    assert rank_options(opts)[0]["id"] == "opt_early_two"