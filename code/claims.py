"""Claim checking for model prose.

A model's explanation ships only when every checkable claim in it is reproduced
by the deterministic decision. This is the same shape as the OCR trust rule: the
model may phrase things, but it may not add facts.

What counts as a claim:

* **money** — every figure, whatever its length and whether or not a currency
  code is glued to it ("ZAR50000"), except the two exempt shapes: a figure that
  is followed by a count unit ("2 payments", "3 days"), and a small count that
  is one side of a fraction ("3 of 5 samples"). A percentage is exempt on
  purpose: "15% of your balance" is a ratio, not an amount, and the kernel row
  carries no ratio to check it against;
* **dates** — an ISO date;
* **status / method names** — the words that name a decision outcome, in prose
  ("affordable now", "pay in full") or as a literal contract token
  (``not_affordable``);
* **spending-change actions** — a ``stop:`` or ``reduce_to:`` token.

Anything unsupported means the prose is discarded and the kernel text stands.
The check is deliberately conservative: a rejected sentence costs the user a
plainer explanation, while a shipped fabrication costs them money.
"""
from __future__ import annotations

import re
from decimal import Decimal

from code.amounts import to_decimal

# --- what the model may claim ---------------------------------------------

STATUS_PHRASES = {
    "affordable_now": ("affordable now",),
    "affordable_with_plan": (
        "affordable with plan",
        "affordable with a plan",
        "affordable with the plan",
    ),
    "affordable_later": ("affordable later",),
    "not_affordable": ("not affordable",),
}

METHOD_PHRASES = {
    "full_payment": ("full payment", "pay in full", "paying in full"),
    "partial_payment": ("partial payment", "pay part", "paying part"),
    "installments": (
        "installments",
        "instalments",
        "installment plan",
        "instalment plan",
    ),
    "wait": ("wait", "waiting"),
    "not_recommended": ("not recommended",),
}

#: A currency code glued to the digits ("ZAR50000", "Rp2.000.000") is a prefix,
#: not a word boundary, so the code is blanked before the scan and the digits
#: become visible. Lengths are preserved, so token offsets stay valid.
_CURRENCY_CODES = (
    "zar", "idr", "usd", "eur", "gbp", "sgd", "myr", "thb", "php", "inr",
    "jpy", "aud", "cad", "chf", "rp",
)
#: A currency marker, before or after the digits, is a prefix rather than a word
#: boundary. "R" is the repo's own ZAR symbol (see code/amounts.py), and
#: "R88000" and "88000ZAR" are both ways a model writes an amount.
_GLUED_CURRENCY = re.compile(
    rf"(?i)(?:\b(?:{'|'.join(_CURRENCY_CODES)})(?=\d)"
    rf"|(?<=\d)(?:{'|'.join(_CURRENCY_CODES)})\b)"
    r"|[$\u20ac\u00a3\u00a5\u20b9]|(?<!\w)R(?=\d)"
)
#: A shorthand multiplier is still a figure: "50k" is fifty thousand.
_SHORTHAND = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s?([kKmMbB])(?![\w])")
_SHORTHAND_FACTOR = {"k": 1000, "m": 1000000, "b": 1000000000}
#: Dot-grouped thousands ("2.000.000") are one figure, not a decimal.
#: Month names a model may write, so "12 September" and "the 12th of September"
#: are dates rather than a figure followed by a word.
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_WORD = "|".join(sorted(_MONTHS, key=len, reverse=True))
#: A written date in either order, with an optional day suffix and year.
_SPELLED_DATE = re.compile(
    rf"\b(?:(?P<day1>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?"
    rf"(?P<month1>{_MONTH_WORD})(?:,?\s+(?P<year1>\d{{4}}))?"
    rf"|(?P<month2>{_MONTH_WORD})\s+(?P<day2>\d{{1,2}})(?:st|nd|rd|th)"
    rf"(?:,?\s+(?P<year3>\d{{4}}))?|(?P<month3>{_MONTH_WORD})\s+(?P<day3>\d{{1,2}}),?\s+(?P<year3b>\d{{4}}))\b",
    re.IGNORECASE,
)
_MONEY = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:\.\d{3})+|\d[\d,]*(?:\.\d+)?)(?![\w])"
)
#: The same figure with its thousands separators: "2.000.000" is two million.
_DOT_GROUPED = re.compile(r"\d{1,3}(?:\.\d{3})+")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# A figure followed by a count noun is a quantity, not an amount: "2
# installments" and "3 of 5 samples" must not become money claims.
# The second figure in "3 of 5 samples" is the same count as the first. Only a
# small leading count qualifies, so "paid 500 of 2000" still checks 2000.
_COUNT_PREFIX = re.compile(r"(?<![\d.])\d{1,2}\s+of\s*$", re.IGNORECASE)
_COUNT_UNIT = re.compile(
    r"\s*(?:install?ments?|payments?|months?|weeks?|days?|years?|times|percent|%)",
    re.IGNORECASE,
)
#: The leading figure of a fraction is a count only when another count follows
#: it: "3 of 5 samples" is a fraction, "2000 of your balance" is money.
_COUNT_FOLLOWS = re.compile(r"\s+of\s+\d", re.IGNORECASE)
_ACTION = re.compile(
    r"\b(stop|reduce_to):(event_\d+)(?::(\d+(?:\.\d+)?))?", re.IGNORECASE
)
# Underscored contract tokens are unambiguous; plain words like "installments"
# are already covered by METHOD_PHRASES and must not be counted twice.
_SNAKE = re.compile(r"\b(affordable_now|affordable_with_plan|affordable_later|"
                    r"not_affordable|full_payment|partial_payment|"
                    r"not_recommended)\b")


def _decimal(value) -> Decimal | None:
    return to_decimal(value, None, strip_grouping=True)


def _supported_numbers(row: dict, extra_numbers=()) -> set[Decimal]:
    numbers: set[Decimal] = set()
    for value in row.values():
        number = _decimal(value)
        if number is not None:
            numbers.add(number)
    # An installment or partial plan is a `date:amount|date:amount` string, so
    # each leg amount has to be added explicitly: the whole string is not a
    # number, and the legs are exactly what a plan explanation cites.
    # "reduce_to:<event>:<amount>" carries the amount a change is allowed to
    # move to; a model that explains the change has to be able to cite it.
    for action in str(row.get("spending_changes_needed") or "").split("|"):
        parts = action.split(":")
        if len(parts) >= 3:
            number = _decimal(parts[-1])
            if number is not None:
                numbers.add(number)
    for leg in str(row.get("payment_plan") or "").split("|"):
        _, _, amount = leg.partition(":")
        number = _decimal(amount)
        if number is not None:
            numbers.add(number)
    for value in extra_numbers or ():
        number = _decimal(value)
        if number is not None:
            numbers.add(number)
    return numbers


def _supported_dates(row: dict, extra_dates=()) -> set[str]:
    dates = set()
    for value in row.values():
        for match in _ISO_DATE.finditer(str(value or "")):
            dates.add(match.group(0))
    for value in extra_dates or ():
        text = str(value or "").strip()
        if _ISO_DATE.fullmatch(text):
            dates.add(text)
    return dates


def _money_claims(text: str) -> list[tuple[str, Decimal]]:
    # An ISO date is a date, not an amount: blank the date spans first so the
    # year/month/day digits cannot be read as money.
    unspanned = _GLUED_CURRENCY.sub(lambda match: " " * len(match.group(0)), text)
    unspanned = _ISO_DATE.sub(lambda match: " " * len(match.group(0)), unspanned)
    # The same for a written date: "12 September 2025" is a date, and its
    # day and year are not amounts.
    unspanned = _SPELLED_DATE.sub(lambda match: " " * len(match.group(0)), unspanned)
    # "50k" is fifty thousand: read the multiplier, then blank the span so the
    # "50" is not claimed as a bare fifty.
    shorthand = []
    for match in _SHORTHAND.finditer(unspanned):
        number = _decimal(match.group(1))
        if number is not None:
            shorthand.append(
                (match.group(0), number * _SHORTHAND_FACTOR[match.group(2).lower()])
            )
        unspanned = (
            unspanned[: match.start()]
            + " " * (match.end() - match.start())
            + unspanned[match.end():]
        )
    claims = []
    for match in _MONEY.finditer(unspanned):
        token = text[match.start():match.end()]
        # A figure followed by a count noun is a quantity, not money: "2
        # installments", "3 of 5 samples". Every other figure is a claim. A
        # word list cannot be an invariant: "The ceiling is 200 today" invents
        # an amount with no money word anywhere near it, and 141 of the 250
        # requests have a safe amount under 1000.
        significant = token.replace(",", "").replace(".", "")
        if _COUNT_UNIT.match(unspanned[match.end():]) or _COUNT_PREFIX.search(
            unspanned[:match.start()]
        ):
            continue
        if _COUNT_FOLLOWS.match(unspanned[match.end():]) and len(significant) <= 2:
            continue
        if _DOT_GROUPED.fullmatch(token):
            number = to_decimal(token.replace(".", ""), None)
        else:
            number = _decimal(token)
        if number is not None:
            claims.append((token, number))
    return shorthand + claims


def _phrase_claims(text: str, table: dict[str, tuple[str, ...]]) -> list[tuple[str, str]]:
    """(matched phrase, canonical name) for every phrase found, longest first."""
    lowered = text.lower()
    ordered = sorted(
        ((variant, canonical) for canonical, variants in table.items() for variant in variants),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    found: list[tuple[str, str]] = []
    consumed: list[tuple[int, int]] = []
    for variant, canonical in ordered:
        for match in re.finditer(rf"\b{re.escape(variant)}\b", lowered):
            span = match.span()
            if any(start <= span[0] < end for start, end in consumed):
                continue
            consumed.append(span)
            found.append((variant, canonical))
    return found


def _normalise_action(token: str) -> str:
    kind, event_id, amount = _ACTION.fullmatch(token.strip()).groups()
    kind = kind.lower()
    if kind == "stop":
        return f"stop:{event_id}"
    return f"reduce_to:{event_id}:{_decimal(amount)}"


_NO_CHANGES = re.compile(
    r"\b(?:no|without any|needs? no)\s+(?:spending\s+)?changes?\b"
    r"|\bnothing\b[^.]{0,20}\b(?:stop|cut|reduce)\b",
    re.IGNORECASE,
)


def _spelled_date(match) -> tuple[int, int, int | None] | None:
    """(month, day, year-or-None) for a written date, or None if unreadable."""
    day = match.group("day1") or match.group("day2") or match.group("day3")
    month = match.group("month1") or match.group("month2") or match.group("month3")
    year = match.group("year1") or match.group("year3") or match.group("year3b")
    if not day or not month:
        return None
    number = _MONTHS.get(str(month).lower())
    if number is None:
        return None
    return number, int(day), (int(year) if year else None)


def _date_parts(dates) -> list[tuple[int, int, int]]:
    parts = []
    for value in dates:
        try:
            year, month, day = (int(part) for part in str(value).split("-"))
        except ValueError:
            continue
        parts.append((month, day, year))
    return parts


def verify_claims(
    text,
    row: dict,
    *,
    extra_numbers=(),
    extra_dates=(),
) -> dict:
    """Check every claim in ``text`` against the kernel row.

    Returns ``{"ok", "checked", "unsupported", "rejected"}`` where ``unsupported``
    lists each claim the row does not reproduce.
    """
    prose = str(text or "").strip()
    report = {"ok": False, "checked": 0, "unsupported": [], "rejected": False}

    if not prose or not isinstance(row, dict):
        return report

    numbers = _supported_numbers(row, extra_numbers)
    dates = _supported_dates(row, extra_dates)
    kernel_status = str(row.get("affordability_status") or "")
    kernel_method = str(row.get("recommended_payment_method") or "")
    kernel_changes = str(row.get("spending_changes_needed") or "none")
    kernel_actions = {
        _normalise_action(match.group(0))
        for match in _ACTION.finditer(kernel_changes)
    }

    unsupported: list[dict] = []
    checked = 0

    for token, value in _money_claims(prose):
        checked += 1
        if value not in numbers:
            unsupported.append({"kind": "amount", "text": token, "expected": sorted(
                _fmt(item) for item in numbers
            )})
    for match in _ISO_DATE.finditer(prose):
        checked += 1
        if match.group(0) not in dates:
            unsupported.append({"kind": "date", "text": match.group(0),
                                "expected": sorted(dates)})
    spelled = _date_parts(dates)
    for match in _SPELLED_DATE.finditer(prose):
        parsed = _spelled_date(match)
        if parsed is None:
            continue
        month, day, year = parsed
        checked += 1
        if not any(
            month == item_month
            and day == item_day
            and (year is None or year == item_year)
            for item_month, item_day, item_year in spelled
        ):
            unsupported.append({"kind": "date", "text": match.group(0),
                                "expected": sorted(dates)})

    for variant, canonical in _phrase_claims(prose, STATUS_PHRASES):
        checked += 1
        if canonical != kernel_status:
            unsupported.append({"kind": "status", "text": variant,
                                "expected": kernel_status})
    for variant, canonical in _phrase_claims(prose, METHOD_PHRASES):
        checked += 1
        if canonical != kernel_method:
            unsupported.append({"kind": "method", "text": variant,
                                "expected": kernel_method})
    for match in _SNAKE.finditer(prose):
        token = match.group(1)
        canonical = token
        expected = kernel_status if canonical in STATUS_PHRASES else kernel_method
        if token in STATUS_PHRASES or token in METHOD_PHRASES:
            checked += 1
            if canonical != expected:
                unsupported.append({"kind": "token", "text": token,
                                    "expected": expected})
    for match in _NO_CHANGES.finditer(prose):
        # "No spending changes are needed" contradicts a row that stops an
        # event, so the explanation would disagree with its own column.
        checked += 1
        if kernel_changes not in {"", "none"}:
            unsupported.append({"kind": "changes", "text": match.group(0),
                                "expected": kernel_changes})
    for match in _ACTION.finditer(prose):
        checked += 1
        if _normalise_action(match.group(0)) not in kernel_actions:
            unsupported.append({"kind": "action", "text": match.group(0),
                                "expected": kernel_changes})

    report["checked"] = checked
    report["unsupported"] = unsupported
    # Shipping needs zero unsupported claims. Prose with nothing checkable is
    # allowed: it asserts nothing that can be contradicted by the ledger.
    report["ok"] = not unsupported
    report["rejected"] = bool(unsupported)
    return report


def _fmt(value) -> str:
    return f"{value:f}"


def describe_rejection(report: dict) -> str:
    """One line naming the first unsupported claim, for the repair loop."""
    unsupported = (report or {}).get("unsupported") or []
    if not unsupported:
        return "the explanation carried no verifiable claim"
    first = unsupported[0]
    return (
        f"the explanation claimed {first.get('kind')} {first.get('text')!r}, "
        f"which the decision does not support (expected {first.get('expected')})"
    )


__all__ = ["describe_rejection", "verify_claims"]
