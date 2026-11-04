"""P1 amount router + lexicon (TDD): locale-aware parse and currency-coded extraction.

parse_amount mirrors the suite's normalize_amount oracle behavior so the two can never
drift: spec_checks.normalize_amount delegates here. extract_amounts finds currency-coded
figures in free text (ISO code or symbol) after stripping ID citations, so a trailing
reference id can never surface as a monetary figure.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

ISO_CODES = {"USD", "EUR", "ZAR", "IDR", "INR"}
SYMBOLS = {"$": "USD", chr(0x20AC): "EUR", "R": "ZAR", chr(0x20B9): "INR"}
_CURRENCY_TOKEN = re.compile(r"(?:" + "|".join(sorted(ISO_CODES)) + r")|[$€R₹]")
_REF = re.compile(r"\b(?:event|request)_\w+\b")
_GROUPED = re.compile(r"\d[\d.,-]*")
_ENDS = re.compile(r"[^\d.,\-]")


def _parse(raw: str, currency: str, lang: str) -> Decimal | None:
    s = (raw or "").strip().replace(" ", "")
    if not s:
        return None
    cur = (currency or "").upper()
    lang = (lang or "").lower()
    
    # Robust separator logic:
    # 1. If only one type of separator exists, treat as grouping.
    # 2. If both exist, the last one is the decimal.
    # 3. If none exist, it's a plain number.
    dots = s.count(".")
    commas = s.count(",")
    
    if dots > 0 and commas > 0:
        # Mixed: last one is decimal
        last_dot = s.rfind(".")
        last_comma = s.rfind(",")
        if last_dot > last_comma:
            s = s.replace(",", "") # US style
        else:
            s = s.replace(".", "").replace(",", ".") # EU style
    elif dots > 1:
        s = s.replace(".", "") # dot grouping
    elif commas > 1:
        s = s.replace(",", "") # comma grouping
    elif dots == 1 and commas == 0:
        # Ambiguous single dot: IDR/ES style uses dot as grouping "1.000"
        # but US uses dot as decimal "1.000".
        if cur in {"IDR"} or lang.startswith("id") or (cur == "EUR" and lang.startswith("es")):
            # Heuristic: if dot is at index -3, it's a decimal. Else group.
            if not s.endswith(".00") and s.rfind(".") != len(s)-3:
                s = s.replace(".", "")
    elif commas == 1 and dots == 0:
        # Ambiguous single comma: "1,000" (group) vs "1,00" (decimal)
        if cur in {"IDR"} or lang.startswith("id") or (cur == "EUR" and lang.startswith("es")):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
            
    try:
        d = Decimal(s)
        if not d.is_finite():
            return None
        return d
    except InvalidOperation:
        return None


def parse_amount(raw: str, currency: str, lang: str = "") -> Decimal | None:
    return _parse(raw, currency, lang)


def to_decimal(value, default=None, *, strip_grouping: bool = False):
    """Coerce one scalar to a finite :class:`Decimal`, else return ``default``.

    This is the single place that turns a spreadsheet cell, a CSV string or a
    model answer into a number.  ``None``, a bool, ``""``, ``"nan"``, NaN and
    Infinity all fall back to ``default`` rather than leaking a non-finite value
    into the solver.

    ``strip_grouping`` removes thousands separators, for a figure lifted out of
    free text ("1,234.50").  It is opt-in because a locale-aware path must keep
    the separator it is about to interpret (see :func:`parse_amount`).
    """
    if value is None or isinstance(value, bool):
        return default
    text = str(value)
    if strip_grouping:
        text = text.replace(",", "")
    text = text.strip()
    if not text or text.lower() == "nan":
        return default
    try:
        number = Decimal(text)
    except (InvalidOperation, TypeError, ValueError):
        return default
    return number if number.is_finite() else default


def strict_decimal(value) -> Decimal | None:
    """Parse one scalar as a finite :class:`Decimal`, else return ``None``.

    Stricter than :func:`to_decimal`: this helper never substitutes a default
    and never guesses.  ``None``, a bool, a blank/whitespace string, ``"nan"``,
    NaN, Infinity and unparseable text are all rejected with ``None`` so the
    kernel can fail closed instead of defaulting an invalid user value to zero.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def extract_amounts(text: str, lang: str = "") -> list[dict]:
    text = (text or "").strip()
    text = _REF.sub(" ", text)
    _ADJ = re.compile(r"[\s,;-]*")
    out = []
    for m in _CURRENCY_TOKEN.finditer(text):
        token = m.group(0)
        currency = token.upper() if token in ISO_CODES else SYMBOLS[token]
        trailing = _GROUPED.search(text[m.end():m.end() + 24])
        leading = None
        if trailing is None:
            head = text[max(0, m.start() - 24):m.start()]
            for nm in reversed(list(_GROUPED.finditer(head))):
                if _ADJ.fullmatch(text[nm.end():m.start()]):
                    leading = nm
                    break
        n = trailing or leading
        if n is None:
            continue
        num = _ENDS.split(n.group(0))[0]
        amt = _parse(num, currency, lang)
        if amt is None:
            continue
        out.append({"currency": currency, "amount": amt})
    return out