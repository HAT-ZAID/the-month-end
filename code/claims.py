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
from decimal import ROUND_DOWN, Decimal

from code.amounts import to_decimal

# --- what the model may claim ---------------------------------------------

#: A model writes the decision in ordinary English, not only in the contract's
#: own words. Each variant maps to the value it asserts; ``verify_claims``
#: compares that value with the kernel's, so a sentence that denies an affordable
#: request is caught by the status it actually claims.
STATUS_PHRASES = {
    "affordable_any": ("affordable", "can afford", "could afford", "afford"),
    "affordable_now": ("affordable now", "affordable today"),
    "affordable_with_plan": (
        "affordable with plan",
        "affordable with a plan",
        "affordable with the plan",
        "affordable with some changes",
    ),
    "affordable_later": (
        "affordable later",
        "affordable next",
        "not affordable yet",
        "not affordable now",
        "not affordable today",
        "cannot afford it now",
    ),
    "not_affordable": (
        "not affordable",
        "cannot afford",
        "can't afford",
        "could not afford",
        "unaffordable",
        "not be affordable",
    ),
}

METHOD_PHRASES = {
    "full_payment": (
        "full payment",
        "pay in full",
        "paying in full",
        "pay the full amount",
        "full amount",
        "whole amount",
        "in full",
        "all at once",
        "one payment",
        "single payment",
    ),
    "partial_payment": (
        "partial payment",
        "pay part",
        "paying part",
        "pay some now",
        "part now",
    ),
    "installments": (
        "installments",
        "instalments",
        "installment",
        "instalment",
        "installment plan",
        "instalment plan",
        "in installments",
        "in instalments",
        "in one installment",
        "in one instalment",
        "single installment",
        "single instalment",
    ),
    "wait": ("wait", "waiting", "wait until"),
    "not_recommended": ("not recommended", "do not proceed", "should not proceed"),
}

#: Money fields the CSV writes at cent precision.
_CENT_FIELDS = ("amount_safe_to_pay",)
#: Fields whose money arrives inside a string ("date:amount" legs, actions).
_CENT_LEG_FIELDS = ("payment_plan", "spending_changes_needed")

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
#: A bare ordinal day ("on the 14th"): a date claim with no month, checked
#: against the day-of-month of the row's own dates.
_DAY_ORDINAL = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE)

#: A figure written in words. The row prints digits, so prose that spells an
#: amount is a claim it cannot support.
_NUMBER_WORD = re.compile(
    r"\b(?:"
    r"(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)[-\s]"
    r"(?:one|two|three|four|five|six|seven|eight|nine)"
    r"|(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)\s+"
    r"(?:hundred|thousand|million|lakh|crore)"
    r")\b",
    re.IGNORECASE,
)
#: A relative date. The row holds fixed dates, so prose that only says "next
#: month" names a date nothing can confirm.
_RELATIVE_DATE = re.compile(
    r"\b(?:tomorrow|next\s+(?:week|month|year)"
    r"|in\s+\d+\s+(?:days?|weeks?|months?|years?)"
    r"|end\s+of\s+(?:the\s+)?(?:month|week|year|january|february|march|april|may|"
    r"june|july|august|september|october|november|december))\b",
    re.IGNORECASE,
)

#: A written date in either order, with an optional day suffix and year.
_SPELLED_DATE = re.compile(
    rf"\b(?:(?P<day1>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?"
    rf"(?P<month1>{_MONTH_WORD})(?:,?\s+(?P<year1>\d{{4}}))?"
    rf"|(?P<month2>{_MONTH_WORD})\s+(?P<day2>\d{{1,2}})(?:st|nd|rd|th)"
    rf"(?:,?\s+(?P<year3>\d{{4}}))?|(?P<month3>{_MONTH_WORD})\s+"
    rf"(?P<day3>\d{{1,2}}),?\s+(?P<year3b>\d{{4}})"
    rf"|(?P<month4>{_MONTH_WORD})\s+(?P<day4>\d{{1,2}})(?!\d))\b",
    re.IGNORECASE,
)
_MONEY = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:\.\d{3})+|\d[\d,]*(?:\.\d+)?)(?!\d|st\b|nd\b|rd\b|th\b)"
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
    r"\s*(?:install?ments?|payments?|months?|weeks?|days?|years?|times|percent)\b"
    r"|\s*%",
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


def _to_cents(number: Decimal) -> Decimal:
    """The cents ``output.csv`` writes for this figure."""
    return number.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def cites_every_action(prose, changes) -> bool:
    """True when the prose names every action the row requires.

    A sentence that denies a required change and a sentence that describes one
    are both ordinary English, so no deny-list converges.  What is decidable is
    whether the explanation names what the row actually does; on a row with an
    action, that is the rule.
    """
    wanted = []
    for action in str(changes or "").split("|"):
        kind, _, rest = action.partition(":")
        event_id = rest.partition(":")[0].strip()
        if kind.strip() in {"stop", "reduce_to"} and event_id:
            wanted.append(event_id)
    if not wanted:
        return True
    text = str(prose or "")
    # Complete identifiers only: one event id must not be satisfied by a
    # longer one, or a sentence about a different event would excuse the event
    # the row actually requires.
    return all(
        re.search(rf"(?<!\w){re.escape(event_id)}(?!\w)", text, re.IGNORECASE)
        for event_id in wanted
    )


def _supported_numbers(row: dict, extra_numbers=()) -> set[Decimal]:
    numbers: set[Decimal] = set()
    for field, value in row.items():
        number = _decimal(value)
        if number is None:
            continue
        if field in _CENT_FIELDS:
            # ``output.csv`` ships cents, so that is the figure a correct
            # explanation cites; the solver's full precision is not.
            number = _to_cents(number)
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
                numbers.add(_to_cents(number))
    for leg in str(row.get("payment_plan") or "").split("|"):
        _, _, amount = leg.partition(":")
        number = _decimal(amount)
        if number is not None:
            numbers.add(_to_cents(number))
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
    # Only a date the checker can verify is blanked.  A modal "14 may be early"
    # must stay visible to the amount scan, or neither check would see the 14.
    span_pieces = list(unspanned)
    for match in _SPELLED_DATE.finditer(unspanned):
        if _spelled_date(match) is not None:
            for index in range(match.start(), match.end()):
                span_pieces[index] = " "
    unspanned = "".join(span_pieces)
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
        if _COUNT_UNIT.match(unspanned[match.end():]) or (
            len(significant) <= 2 and _COUNT_PREFIX.search(unspanned[:match.start()])
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


def _phrase_claims(
    text: str, table: dict[str, tuple[str, ...]]
) -> list[tuple[str, str, tuple[int, int]]]:
    """(phrase, canonical name, span) for every match, longest phrase first."""
    lowered = text.lower()
    ordered = sorted(
        ((variant, canonical) for canonical, variants in table.items() for variant in variants),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    found: list[tuple[str, str, tuple[int, int]]] = []
    consumed: list[tuple[int, int]] = []
    for variant, canonical in ordered:
        for match in re.finditer(rf"\b{re.escape(variant)}\b", lowered):
            span = match.span()
            if any(start <= span[0] < end for start, end in consumed):
                continue
            before = lowered[: span[0]].rstrip()
            if before.endswith("not") and canonical.startswith("affordable"):
                continue  # "not affordable today" is the negative variant
            consumed.append(span)
            found.append((variant, canonical, span))
    return found


def _normalise_action(token: str) -> str:
    kind, event_id, amount = _ACTION.fullmatch(token.strip()).groups()
    kind = kind.lower()
    if kind == "stop":
        return f"stop:{event_id}"
    if amount is None:
        # An incomplete action names no figure. It can never equal a kernel
        # action, which always carries one, so it is reported as a claim
        # instead of raising inside the comparison.
        return f"reduce_to:{event_id}:"
    # ``output.csv`` prints the action at cents, so that is the token a correct
    # explanation can cite. The amount scan still rejects an unrounded figure.
    return f"reduce_to:{event_id}:{_to_cents(_decimal(amount))}"


#: A denial of a required change, matched by shape rather than by enumerating
#: wordings: an auxiliary negation followed by a change verb, an absence-of-change
#: statement about spending, or the field itself set to none.
_NEG_AUX = (
    r"(?:won't|will not|wouldn't|would not|cannot|can't|needn't|need not|no need"
    r"|don't|do not|doesn't|does not|didn't|did not|neither|nor|zero|not)"
)
_CHANGE_VERB = r"(?:cut|reduc|stop|cancel|trim|chang|drop|paus|adjust|modif|touch|alter|mov)\w*"
_NO_CHANGES = re.compile(
    rf"\b{_NEG_AUX}\b[^.]{{0,40}}\b{_CHANGE_VERB}\b"
    rf"|\bwithout(?: any)?\b[^.]{{0,24}}\b{_CHANGE_VERB}\b"
    rf"|\b(?:nothing|no)\b[^.]{{0,24}}\b{_CHANGE_VERB}\b"
    rf"|\b{_CHANGE_VERB}\b[^.]{{0,24}}\b(?:nothing|none|unnecessary)\b"
    r"|\b(?:spending|budget|plan)\b[^.]{0,24}\b(?:can\s+|could\s+|will\s+)?"
    r"\b(?:stay|stays|stayed|remain|remains|remained)\b[^.]{0,24}"
    r"\b(?:as it is|the same|unchanged|intact)\b"
    r"|\b(?:spending|budget|plan|subscriptions?|payments?)\b[^.]{0,40}"
    r"\b(?:unchanged|untouched|the same|as it is|as is|alone|intact|"
    r"(?:can|could|will|would)\s+(?:stay|remain))\b"
    r"|\b(?:your\s+)?(?:spending|budget|plan)\s+(?:is|are|was|were)\s+"
    r"(?:unchanged|the same|intact)\b"
    r"|\bnothing (?:changes|else changes)\b"
    r"|\bno action\b[^.]{0,24}\bspending\b"
    rf"|\b{_CHANGE_VERB}\b[^.]{{0,24}}\b(?:not\s+required|not\s+needed"
    r"|unnecessary)\b"
    r"|\bevent_\d+\b[^.]{0,24}\b(?:can|could|will|would|should)\s+stay\b"
    r"|\bspending_changes_needed\s*[:=]\s*none\b",
    re.IGNORECASE,
)


#: A phrase that narrows a denial to "nothing beyond the required change". The
#: scope word has to attach to the change itself, not to some other noun in the
#: same clause: "but other options include…" narrows nothing.
_SCOPE = re.compile(
    # The qualifier has to attach to a change, not to any noun after it: "no
    # other options include stopping ..." narrows nothing.
    r"\b(?:no|nothing|none|not|without)\s+(?:other|further|additional|more|else)\s+"
    r"(?:spending\s+)?(?:should\s+|would\s+|will\s+|can\s+|could\s+|may\s+|might\s+"
    r"|must\s+)?"
    r"(?:changes?|cuts?|reductions?|trims?|stops?|required|needed|necessary"
    rf"|{_CHANGE_VERB})"
    rf"|\b(?:apart from|except(?: for)?|besides|aside from|other than)\s+"
    rf"(?:the\s+)?{_CHANGE_VERB}",
    re.IGNORECASE,
)
#: The characters that end a clause for scoping purposes.
_CLAUSE_END = ".!?;\n"


def _clause_at(text: str, start: int, end: int) -> str:
    """The clause around ``[start, end)``: scoping does not cross a full stop."""
    left = max((text.rfind(char, 0, start) for char in _CLAUSE_END), default=-1)
    rights = [index for index in (text.find(char, end) for char in _CLAUSE_END) if index >= 0]
    right = min(rights) if rights else len(text)
    return text[left + 1 : right]


def _spelled_date(match) -> tuple[int, int, int | None] | None:
    """(month, day, year-or-None) for a written date, or None if unreadable."""
    day = (
        match.group("day1")
        or match.group("day2")
        or match.group("day3")
        or match.group("day4")
    )
    month = (
        match.group("month1")
        or match.group("month2")
        or match.group("month3")
        or match.group("month4")
    )
    year = match.group("year1") or match.group("year3") or match.group("year3b")
    if not day or not month:
        return None
    number = _MONTHS.get(str(month).lower())
    if number is None:
        return None
    if not year and str(month) != str(month).capitalize():
        # "the 14 may be too early" is a modal, not May: a lower-case month word
        # only counts as a date when a year pins it down.  The caller then leaves
        # those digits visible to the amount check.
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


def states_the_decision(prose, row) -> bool:
    """True when the prose grounds the decision in the row's own words.

    A table of phrases can only reject what it recognises, so the gate asks the
    other question as well: the rationale has to name the status the row holds
    and the method it recommends. A rationale that describes the decision in
    words no table knows is not endorsed; the kernel's own sentence ships
    instead. That is the conservative direction, and it is the only one that
    stops converging on the next paraphrase.
    """
    text = str(prose or "")
    status = str(row.get("affordability_status") or "")
    method = str(row.get("recommended_payment_method") or "")

    if status:
        named = {
            canonical for _, canonical, _span in _phrase_claims(text, STATUS_PHRASES)
        }
        if status not in named and not (
            status.startswith("affordable") and "affordable_any" in named
        ):
            return False

    if method:
        named = {
            canonical for _, canonical, _span in _phrase_claims(text, METHOD_PHRASES)
        }
        if method not in named:
            # "the first safe full payment is <date>" is a statement about the
            # earliest-date column, not a recommendation of full payment.
            lowered = text.lower()
            if method == "full_payment" or not any(
                marker in lowered for marker in ("first safe full payment",
                                                 "earliest full payment",
                                                 "first full payment")
            ):
                return False

    return True


def _cited_date(text: str) -> str | None:
    """The date this text cites as ISO, from either a numeric or written form."""
    match = _ISO_DATE.search(text)
    if match is not None:
        return match.group(0)
    match = _SPELLED_DATE.search(text)
    if match is None:
        return None
    parsed = _spelled_date(match)
    if parsed is None:
        return None
    month, day, year = parsed
    if year is None:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


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
    spelled_spans: list[tuple[int, int]] = []
    for match in _SPELLED_DATE.finditer(prose):
        parsed = _spelled_date(match)
        if parsed is None:
            continue
        spelled_spans.append(match.span())
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
    # "on the 14th" names a day with no month. It is a date claim, checked
    # against the days the row actually holds, unless a spelled date already
    # covered it.
    for match in _DAY_ORDINAL.finditer(prose):
        if any(start <= match.start() < end for start, end in spelled_spans):
            continue
        # "1st installment" is a count, not a day.
        if _COUNT_UNIT.match(prose[match.end():]):
            continue
        checked += 1
        if int(match.group(1)) not in {item_day for _, item_day, _ in spelled}:
            unsupported.append({"kind": "date", "text": match.group(0),
                                "expected": sorted(dates)})

    word_figures = [
        match
        for match in _NUMBER_WORD.finditer(prose)
        if not _COUNT_UNIT.match(prose[match.end() :])
    ]
    if word_figures:
        checked += 1
        # The row prints digits only, so a spelled figure is unsupported.
        unsupported.append({"kind": "amount", "text": word_figures[0].group(0),
                            "expected": sorted(_fmt(item) for item in numbers)})
    for relative in _RELATIVE_DATE.finditer(prose):
        checked += 1
        unsupported.append({"kind": "date", "text": relative.group(0),
                            "expected": sorted(dates)})

    wanted_ids = {
        item.lower()
        for item in re.findall(r"(?<!\w)event_\d+(?!\w)", kernel_changes, re.IGNORECASE)
    }
    for match in re.finditer(r"(?<!\w)event_\d+(?!\w)", prose, re.IGNORECASE):
        if match.group(0).lower() in wanted_ids:
            continue
        # A rationale may not name an event the row does not touch: that is an
        # instruction about somebody else's spending.
        checked += 1
        unsupported.append({"kind": "action", "text": match.group(0),
                            "expected": kernel_changes})

    for variant, canonical, _span in _phrase_claims(prose, STATUS_PHRASES):
        checked += 1
        if canonical == "affordable_any":
            # The bare adjective says "this is affordable in some form", which
            # every affordable_* status satisfies and not_affordable does not.
            if kernel_status.startswith("affordable"):
                continue
            unsupported.append({"kind": "status", "text": variant,
                                "expected": kernel_status})
            continue
        if canonical != kernel_status:
            unsupported.append({"kind": "status", "text": variant,
                                "expected": kernel_status})
    for variant, canonical, span in _phrase_claims(prose, METHOD_PHRASES):
        if canonical == "full_payment":
            # "the first safe full payment is <date>" speaks about the earliest
            # date column, not about recommending full payment. The span is the
            # match itself, so a repeated phrase is judged where it appears.
            before = prose[: span[0]].rstrip().lower()
            following = prose[span[1] :][:48]
            if before.endswith(("first safe", "earliest", "first")) and re.match(
                r"\s+(?:is|falls on|lands on)\s+", following, re.IGNORECASE
            ):
                # A statement about the earliest date column has to cite that
                # column: a date the row holds elsewhere (a plan leg, the
                # request date) is a different claim. A written date counts,
                # because that is how a model usually writes it.
                cited = _cited_date(following)
                expected = str(row.get("earliest_date_for_full_payment") or "").strip()
                checked += 1
                if cited is None or not expected or cited != expected:
                    unsupported.append({"kind": "date",
                                        "text": cited or variant,
                                        "expected": expected})
                continue
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
        if kernel_changes not in {"", "none"} and _SCOPE.search(
            # The scoping word can sit just past the match ("nothing else
            # changes" matches as far as "nothing"), but it has to be in the
            # same clause: a scoping word in the *next* sentence does not
            # qualify this denial.
            _clause_at(prose, match.start(), match.end())
        ):
            # Only a denial that is explicitly scoped to *no other* changes is
            # excused: "stop the one event and nothing else changes" is true,
            # while "no need to stop the event" names the action and still
            # asserts the opposite, so naming it no longer buys an exemption.
            continue
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
