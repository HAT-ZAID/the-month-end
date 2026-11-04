"""Semantic comparison of the six machine fields in ``output.csv``.

The string comparator this replaces treated ``100`` vs ``100.00`` as a
mismatch, read pandas ``NaN`` as the literal text ``NaN`` and compared
spending-change actions in file order.  This module normalizes each field per
AGENTS.md 6.2 / problem_statement.md before comparing:

* amounts are Decimals compared within one cent;
* status and method vocabulary must match exactly;
* payment plans parse into dated legs and compare in order, amounts within
  one cent, with all no-plan sentinels (``none``/blank/``NaN``/``None``)
  equivalent;
* earliest dates normalize blank/``NaN``/``None`` to "no date" and otherwise
  compare as exact ISO dates;
* spending changes canonicalize into ``stop:<event>`` and
  ``reduce_to:<event>:<amount>`` actions compared as an unordered set.

The module is pure Python (no pandas) so tests and report code can use it
without touching the decision pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re

MACHINE_FIELDS = (
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
)

STATUSES = (
    "affordable_now",
    "affordable_with_plan",
    "affordable_later",
    "not_affordable",
)
METHODS = (
    "full_payment",
    "partial_payment",
    "installments",
    "wait",
    "not_recommended",
)

# Optimism order used by the safety gate: a status is an "upgrade" when the
# prediction promises more affordability than the reference row.
STATUS_OPTIMISM = {
    "not_affordable": 0,
    "affordable_later": 1,
    "affordable_with_plan": 2,
    "affordable_now": 3,
}

AMOUNT_TOLERANCE = Decimal("0.01")

_NO_VALUE = frozenset({"", "none", "nan", "null", "nat", "<na>"})
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PLAN_LEG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}):(\d+(?:\.\d+)?)$")
_STOP_RE = re.compile(r"^stop:([A-Za-z0-9_]+)$")
_REDUCE_RE = re.compile(r"^reduce_to:([A-Za-z0-9_]+):(\d+(?:\.\d+)?)$")


def _text(value) -> str:
    """Strip a cell and turn every no-value sentinel into the empty string."""
    if value is None:
        return ""
    if isinstance(value, float) and value != value:  # NaN
        return ""
    text = str(value).strip()
    if text.lower() in _NO_VALUE:
        return ""
    return text


def has_value(value) -> bool:
    """True when a cell carries text other than the no-value sentinels."""
    return bool(_text(value))


def _canonical_amount(amount: Decimal) -> str:
    text = format(amount.normalize(), "f")
    return text if text not in {"", "-0"} else "0"


def parse_amount(value) -> Decimal | None:
    text = _text(value)
    if not text:
        return None
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() else None


def amounts_equal(actual, expected, tolerance: Decimal = AMOUNT_TOLERANCE) -> bool:
    left, right = parse_amount(actual), parse_amount(expected)
    if left is None or right is None:
        return left is None and right is None
    return abs(left - right) <= tolerance


def statuses_equal(actual, expected) -> bool:
    return str(actual) == str(expected)


def methods_equal(actual, expected) -> bool:
    return str(actual) == str(expected)


def parse_date(value) -> date | None:
    text = _text(value)
    if not text or not _DATE_RE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def dates_equal(actual, expected) -> bool:
    actual_text, expected_text = _text(actual), _text(expected)
    if not actual_text and not expected_text:
        return True
    actual_date, expected_date = parse_date(actual_text), parse_date(expected_text)
    if actual_date is None or expected_date is None:
        return False
    return actual_date == expected_date


@dataclass(frozen=True)
class PlanLeg:
    day: date
    amount: Decimal


def parse_plan(value) -> tuple[PlanLeg, ...]:
    """Parse ``YYYY-MM-DD:amount`` legs. Empty/no-plan sentinels return ``()``.

    Raises ``ValueError`` for malformed legs so a prediction can never match a
    reference row by accident.
    """
    text = _text(value)
    if not text:
        return ()
    legs: list[PlanLeg] = []
    for raw in text.split("|"):
        part = raw.strip()
        match = _PLAN_LEG_RE.match(part)
        if not match:
            raise ValueError(f"malformed payment_plan leg {part!r}")
        day = parse_date(match.group(1))
        if day is None:
            raise ValueError(f"invalid payment_plan date {match.group(1)!r}")
        amount = Decimal(match.group(2))
        if not amount.is_finite():
            raise ValueError(f"invalid payment_plan amount {match.group(2)!r}")
        legs.append(PlanLeg(day, amount))
    return tuple(legs)


def plans_equal(actual, expected) -> bool:
    actual_text, expected_text = _text(actual), _text(expected)
    if not actual_text and not expected_text:
        return True
    try:
        actual_legs = parse_plan(actual_text)
    except (ValueError, InvalidOperation):
        return False
    try:
        expected_legs = parse_plan(expected_text)
    except (ValueError, InvalidOperation):
        return False
    if len(actual_legs) != len(expected_legs):
        return False
    for left, right in zip(actual_legs, expected_legs):
        if left.day != right.day:
            return False
        if not amounts_equal(left.amount, right.amount):
            return False
    return True


def canonical_plan(value) -> str:
    legs = parse_plan(value)
    if not legs:
        return "none"
    return "|".join(
        f"{leg.day.isoformat()}:{_canonical_amount(leg.amount)}" for leg in legs
    )


@dataclass(frozen=True)
class ChangeAction:
    kind: str
    event_id: str
    amount: Decimal | None


def parse_spending_changes(value) -> dict[tuple[str, str], ChangeAction]:
    """Parse spending changes into ``{(kind, event_id): ChangeAction}``.

    ``none``/blank/``NaN`` become ``{}``. Duplicate targets and malformed
    actions raise ``ValueError`` (stop/reduce conflict is never silently
    deduplicated).
    """
    text = _text(value)
    if not text:
        return {}
    actions: dict[tuple[str, str], ChangeAction] = {}
    targets: set[str] = set()
    for raw in text.split("|"):
        part = raw.strip()
        if not part:
            raise ValueError("empty spending-change action")
        stop = _STOP_RE.match(part)
        if stop:
            action = ChangeAction("stop", stop.group(1), None)
        else:
            reduce_match = _REDUCE_RE.match(part)
            if not reduce_match:
                raise ValueError(f"malformed spending-change action {part!r}")
            amount = Decimal(reduce_match.group(2))
            if not amount.is_finite():
                raise ValueError(f"invalid spending-change amount {part!r}")
            action = ChangeAction("reduce_to", reduce_match.group(1), amount)
        if action.event_id in targets:
            raise ValueError(f"duplicate spending-change target {action.event_id!r}")
        if len(actions) >= 3:
            raise ValueError("more than three spending-change actions")
        targets.add(action.event_id)
        actions[(action.kind, action.event_id)] = action
    return actions


def spending_changes_equal(actual, expected) -> bool:
    actual_text, expected_text = _text(actual), _text(expected)
    if not actual_text and not expected_text:
        return True
    try:
        actual_actions = parse_spending_changes(actual_text)
    except (ValueError, InvalidOperation):
        return False
    try:
        expected_actions = parse_spending_changes(expected_text)
    except (ValueError, InvalidOperation):
        return False
    if set(actual_actions) != set(expected_actions):
        return False
    for key, right in expected_actions.items():
        left = actual_actions[key]
        if right.kind == "reduce_to" and not amounts_equal(left.amount, right.amount):
            return False
    return True


def canonical_spending_changes(value) -> tuple[str, ...]:
    actions = parse_spending_changes(value)
    canonical = []
    for action in actions.values():
        if action.kind == "stop":
            canonical.append(f"stop:{action.event_id}")
        else:
            canonical.append(
                f"reduce_to:{action.event_id}:{_canonical_amount(action.amount)}"
            )
    return tuple(sorted(canonical))


def status_upgrade(actual_status, expected_status) -> bool:
    actual_rank = STATUS_OPTIMISM.get(str(actual_status))
    expected_rank = STATUS_OPTIMISM.get(str(expected_status))
    if actual_rank is None or expected_rank is None:
        return False
    return actual_rank > expected_rank


def safe_overclaim(actual_amount, expected_amount) -> bool:
    actual = parse_amount(actual_amount)
    expected = parse_amount(expected_amount)
    if actual is None or expected is None:
        return False
    band = max(abs(expected) * Decimal("0.01"), Decimal("1"))
    return actual > expected + band


def field_matches(field: str, actual, expected) -> bool:
    if field == "amount_safe_to_pay":
        return amounts_equal(actual, expected)
    if field == "affordability_status":
        return statuses_equal(actual, expected)
    if field == "recommended_payment_method":
        return methods_equal(actual, expected)
    if field == "payment_plan":
        return plans_equal(actual, expected)
    if field == "earliest_date_for_full_payment":
        return dates_equal(actual, expected)
    if field == "spending_changes_needed":
        return spending_changes_equal(actual, expected)
    raise KeyError(f"unknown machine field {field!r}")


def row_matches(actual: dict, expected: dict) -> bool:
    return all(field_matches(field, actual.get(field), expected.get(field)) for field in MACHINE_FIELDS)


def as_records(rows) -> list[dict]:
    if hasattr(rows, "to_dict"):
        try:
            return [dict(row) for row in rows.to_dict("records")]
        except TypeError:
            pass
    return [dict(row) for row in rows]


def _display(field: str, value) -> str:
    if field == "payment_plan":
        try:
            return canonical_plan(value)
        except (ValueError, InvalidOperation):
            return _text(value)
    if field == "spending_changes_needed":
        try:
            canonical = canonical_spending_changes(value)
        except (ValueError, InvalidOperation):
            return _text(value)
        return "|".join(canonical) if canonical else "none"
    text = _text(value)
    return text


def _safe_actions(value):
    try:
        return parse_spending_changes(value)
    except (ValueError, InvalidOperation):
        return None


def _change_counts(actual_rows, expected_rows) -> dict:
    actual_by_id = {}
    for row in actual_rows:
        request_id = _text(row.get("request_id"))
        actual_by_id.setdefault(request_id, row)

    true_positives = false_positives = false_negatives = 0
    gold_positives = predicted_positives = 0
    gold_changed_rows = predicted_changed_rows = exact_rows = 0
    row_true_positives = row_false_positives = row_false_negatives = 0
    parse_errors: list[dict] = []

    for expected in expected_rows:
        request_id = _text(expected.get("request_id"))
        actual = actual_by_id.get(request_id, {})
        gold_actions = _safe_actions(expected.get("spending_changes_needed"))
        predicted_actions = _safe_actions(actual.get("spending_changes_needed"))

        gold_changed = has_value(expected.get("spending_changes_needed"))
        predicted_changed = has_value(actual.get("spending_changes_needed"))

        if gold_actions is None:
            parse_errors.append({"request_id": request_id, "side": "expected"})
            gold_count = 1 if gold_changed else 0
            gold_actions = {}
        else:
            gold_count = len(gold_actions)
        if predicted_actions is None:
            parse_errors.append({"request_id": request_id, "side": "actual"})
            predicted_count = 1 if predicted_changed else 0
            predicted_actions = {}
        else:
            predicted_count = len(predicted_actions)

        gold_positives += gold_count
        predicted_positives += predicted_count
        if gold_changed:
            gold_changed_rows += 1
        if predicted_changed:
            predicted_changed_rows += 1

        for key, gold_action in gold_actions.items():
            predicted_action = predicted_actions.get(key)
            if predicted_action is not None and (
                gold_action.kind == "stop"
                or amounts_equal(predicted_action.amount, gold_action.amount)
            ):
                true_positives += 1
            else:
                false_negatives += 1
        for key, predicted_action in predicted_actions.items():
            gold_action = gold_actions.get(key)
            if gold_action is None or (
                predicted_action.kind == "reduce_to"
                and not amounts_equal(predicted_action.amount, gold_action.amount)
            ):
                false_positives += 1

        if gold_changed or predicted_changed:
            if spending_changes_equal(
                actual.get("spending_changes_needed"),
                expected.get("spending_changes_needed"),
            ):
                exact_rows += 1
                row_true_positives += 1
            else:
                if predicted_changed:
                    row_false_positives += 1
                if gold_changed:
                    row_false_negatives += 1

    precision = None if predicted_positives == 0 else round(true_positives / predicted_positives, 4)
    recall = None if gold_positives == 0 else round(true_positives / gold_positives, 4)
    row_precision = (
        None
        if (row_true_positives + row_false_positives) == 0
        else round(row_true_positives / (row_true_positives + row_false_positives), 4)
    )
    row_recall = (
        None
        if gold_changed_rows == 0
        else round(row_true_positives / gold_changed_rows, 4)
    )
    return {
        "gold_positives": gold_positives,
        "predicted_positives": predicted_positives,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": precision,
        "recall": recall,
        "rows_with_changes_gold": gold_changed_rows,
        "rows_with_changes_actual": predicted_changed_rows,
        "rows_change_set_exact": exact_rows,
        "row_true_positives": row_true_positives,
        "row_false_positives": row_false_positives,
        "row_false_negatives": row_false_negatives,
        "row_precision": row_precision,
        "row_recall": row_recall,
        "parse_errors": parse_errors,
    }


def compare_rows(actual_rows, expected_rows) -> dict:
    """Compare prediction rows to reference rows field by field.

    ``expected_rows`` defines the row universe (missing predictions count as
    wrong for every field). Output ordering is driven by the reference order
    and ``MACHINE_FIELDS`` so repeated runs produce identical reports.
    """
    actual = as_records(actual_rows)
    expected = as_records(expected_rows)

    actual_by_id: dict[str, dict] = {}
    duplicate_ids: list[str] = []
    for row in actual:
        request_id = _text(row.get("request_id"))
        if request_id in actual_by_id:
            duplicate_ids.append(request_id)
            continue
        actual_by_id[request_id] = row

    expected_ids = [_text(row.get("request_id")) for row in expected]
    expected_id_set = set(expected_ids)
    unexpected_ids = sorted(
        request_id for request_id in actual_by_id if request_id not in expected_id_set
    )
    missing_ids = [request_id for request_id in expected_ids if request_id not in actual_by_id]

    per_field_correct = {field: 0 for field in MACHINE_FIELDS}
    full_row_matches = 0
    mismatches: list[dict] = []

    for expected_row, request_id in zip(expected, expected_ids):
        actual_row = actual_by_id.get(request_id)
        if actual_row is None:
            continue
        row_ok = True
        for field in MACHINE_FIELDS:
            if field_matches(field, actual_row.get(field), expected_row.get(field)):
                per_field_correct[field] += 1
            else:
                row_ok = False
                mismatches.append(
                    {
                        "request_id": request_id,
                        "field": field,
                        "expected": _display(field, expected_row.get(field)),
                        "actual": _display(field, actual_row.get(field)),
                    }
                )
        if row_ok:
            full_row_matches += 1

    spending = _change_counts(
        [actual_by_id[request_id] for request_id in expected_ids if request_id in actual_by_id],
        expected,
    )

    return {
        "total": len(expected),
        "compared": len(expected) - len(missing_ids),
        "full_row_matches": full_row_matches,
        "per_field_correct": per_field_correct,
        "missing_request_ids": missing_ids,
        "unexpected_request_ids": unexpected_ids,
        "duplicate_request_ids": sorted(set(duplicate_ids)),
        "mismatches": mismatches,
        "spending_changes": spending,
    }
