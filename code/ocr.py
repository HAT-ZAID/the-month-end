"""P1 OCR availability routing (TDD).

Layer-0 probe: if the host has no Tesseract binary, never attempt a vision
read and never invent a figure — route to flagged reserve so every downstream
row can report an honest OCR_UNAVAILABLE fallback.
"""
from __future__ import annotations

import os
import re
import shutil
import string
from decimal import Decimal
from pathlib import Path

from code.amounts import ISO_CODES, extract_amounts, parse_amount


_GENERIC_LABELS = (
    r"net\s+pay", r"transferred(?:\s+to)?", r"cash\s+paid", r"total\s+paid",
    r"total\s+amount\s+received", r"amount\s+due(?:\s+till)?",
    r"amount\s+payable", r"grand\s+total", r"settlement\s+amount",
    r"total\s*[:=]", r"amount\s*[:=]", r"amount\b(?!\s+in\s+words?)",
)
_FAMILY_LABELS = {
    "salary": (r"net\s+pay", r"transferred(?:\s+to)?"),
    "receipt": (r"cash\s+paid", r"total\s+paid", r"item\s+bill", r"total\s+order\s+bill", r"grand\s+total", r"balance\s+due", r"^\s*total\b"),
    "bill": (r"amount\s+due\s+till", r"grand\s+total", r"amount\s+payable", r"balance\s+due", r"total\s+amount\s+received", r"total\s*[:=]"),
    "payment_confirmation": (r"total\s+amount\s+received", r"total\s+paid"),
}
_FAMILY_LABEL_GROUPS = {
    "salary": (_FAMILY_LABELS["salary"],),
    "receipt": (
        _FAMILY_LABELS["receipt"][:2],
        (r"item\s+bill",),
        (r"^\s*total\b",),
        (r"grand\s+total",),
    ),
    "bill": (
        (r"amount\s+due(?!\s+(?:till|after))", r"grand\s+total", r"amount\s+payable"),
        (r"amount\s+(?:to\s+be\s+)?received",),
        (r"^\s*total\b",),
    ),
    "payment_confirmation": (
        (r"total\s+amount\s+received", r"total\s+paid"),
        (r"amount\s+received",),
        (r"^\s*total\b",),
    ),
}
_IGNORED_UPPERCASE_WORDS = {
    "NET", "PAY", "TOTAL", "CASH", "PAID", "DUE", "TILL", "AMOUNT", "GRAND",
    "SETTLEMENT", "TRANSferred".upper(), "RECEIVED", "IN", "WORDS", "ONLY",
}
_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_NUMBER_SCALES = {"hundred": 100, "thousand": 1000, "million": 1000000, "billion": 1000000000}
_CURRENCY_WORDS = {"indian", "dollar", "dollars", "euro", "euros", "rand", "rupee", "rupees", "rupiah", "inr", "idr", "usd", "eur", "zar", "paise", "paisa", "cents"}


def _parse_number_words(value: str) -> Decimal | None:
    tokens = re.findall(r"[a-z]+", str(value or "").lower().replace("-", " "))
    tokens = [token for token in tokens if token not in {"and", "only", "paise", "cents"} and token not in _CURRENCY_WORDS]
    raw_tokens = re.findall(r"[a-z]+", str(value or "").lower().replace("-", " "))
    marker = next((index for index, token in enumerate(raw_tokens) if token in {"paise", "paisa", "cents"}), None)
    if marker is None:
        integer_tokens = tokens
        fraction_tokens = []
    else:
        before_fraction = raw_tokens[:marker]
        separator = max((index for index, token in enumerate(before_fraction) if token == "and"), default=None)
        integer_source = before_fraction if separator is None else before_fraction[:separator]
        fraction_source = raw_tokens[marker + 1:] if separator is None else before_fraction[separator + 1:]
        integer_tokens = [token for token in integer_source if token not in {"and", "only"} and token not in _CURRENCY_WORDS]
        fraction_tokens = [token for token in fraction_source if token not in {"and", "only"} and token not in _CURRENCY_WORDS]

    def parse_integer(parts: list[str]) -> int | None:
        if not parts:
            return 0
        if any(token not in _NUMBER_WORDS and token not in _NUMBER_SCALES for token in parts):
            return None
        total = 0
        current = 0
        for token in parts:
            if token in _NUMBER_WORDS:
                current += _NUMBER_WORDS[token]
            elif token == "hundred":
                current = (current or 1) * 100
            else:
                total += (current or 1) * _NUMBER_SCALES[token]
                current = 0
        return total + current

    integer = parse_integer(integer_tokens)
    if integer is None:
        return None
    if marker is None:
        return Decimal(integer)
    fraction = parse_integer(fraction_tokens)
    if fraction is None or fraction > 99:
        return None
    return Decimal(f"{integer}.{fraction:02d}")


def _amount_word_candidate(text: str, expected_currency: str) -> dict | None:
    lines = str(text or "").splitlines()
    fields = []
    for index, line in enumerate(lines):
        if not re.search(r"^\s*amount\s+in(?:\s+words?)?\b|^\s*total\s+in\s+words?\b|^\s*in\s+words\s*[:=]|^\s*total\s*[:=]\s*[A-Za-z]", line, re.IGNORECASE):
            continue
        value = re.sub(r"^\s*(?:amount\s+in(?:\s+words?)?|total\s+in\s+words?|in\s+words|total)\s*[:=-]?\s*", "", line, flags=re.IGNORECASE)
        if not value.strip() and index + 1 < len(lines):
            value = lines[index + 1]
            if index + 2 < len(lines) and "paise" in lines[index + 2].lower():
                value = f"{value} {lines[index + 2]}"
        fields.append(value)
    if len(fields) > 1:
        return None
    if not fields or expected_currency not in ISO_CODES:
        return None
    value = fields[0]
    value = re.sub(rf"\b{re.escape(expected_currency)}\b", " ", value, flags=re.IGNORECASE)
    parsed = _parse_number_words(value)
    if parsed is None:
        return None
    return {"amount": parsed, "currency": expected_currency}


def _extract_amount_from_text(text: str, expected_currency: str, labels: tuple[str, ...]):
    currency = str(expected_currency or "").strip().upper()
    if currency and currency not in ISO_CODES:
        return None
    word_field_present = any(
        re.search(r"^\s*amount\s+in(?:\s+words?)?\b|^\s*total\s+in\s+words?\b|^\s*in\s+words\s*[:=]|^\s*total\s*[:=]\s*[A-Za-z]", line, re.IGNORECASE)
        for line in str(text or "").splitlines()
    )
    word_candidate = _amount_word_candidate(text, currency)
    if word_field_present and word_candidate is None:
        return None
    labeled = []
    saw_mismatched_currency = False
    lines = str(text or "").splitlines()
    for index, line in enumerate(lines):
        if not any(re.search(label, line, re.IGNORECASE) for label in labels):
            continue
        candidate_line = line
        if index + 1 < len(lines) and not re.search(r"\d", line) and re.search(r"(?:[A-Z]{3}|[$\u20ac\u20b9\u00a5R])\s*\d|\d", lines[index + 1]):
            candidate_line = f"{line} {lines[index + 1]}"
        currency_tokens = re.findall(r"\b[A-Z]{3}\b", line)
        unsupported_tokens = [token for token in currency_tokens if token not in ISO_CODES and token not in _IGNORED_UPPERCASE_WORDS]
        if unsupported_tokens:
            saw_mismatched_currency = True
        normalized_line = candidate_line
        if currency == "INR":
            normalized_line = normalized_line.replace("\u20b9", " INR ").replace("\u00a5", " INR ")
        explicit = extract_amounts(normalized_line)
        if currency == "USD":
            decimal_comma = re.findall(r"(?:USD|\$)\s*([0-9]+,[0-9]{2})(?![0-9])", candidate_line, re.IGNORECASE)
            if decimal_comma:
                explicit = [{"currency": "USD", "amount": parse_amount(decimal_comma[0].replace(",", "."), "USD")}]
        mismatched_codes = [token for token in currency_tokens if token in ISO_CODES and token != currency]
        if mismatched_codes:
            saw_mismatched_currency = True
        if currency:
            explicit = [c for c in explicit if c["currency"] == currency]
        if len(explicit) == 1:
            labeled.append(explicit[0])
            continue
        # Some OCR lines lose the currency token; use the event currency only
        # when the labelled line contains exactly one numeric candidate.
        raw_numbers = re.findall(r"(?<![A-Za-z])\d[\d.,]*(?![A-Za-z])", candidate_line)
        if len(raw_numbers) == 1 and currency:
            amount = parse_amount(raw_numbers[0], currency)
            if amount is not None:
                labeled.append({"amount": amount, "currency": currency})
    if saw_mismatched_currency:
        return None
    unique_labeled = {(c["currency"], c["amount"]) for c in labeled}
    if len(unique_labeled) > 1:
        return None
    candidates = extract_amounts(text or "")
    if currency:
        candidates = [c for c in candidates if c["currency"] == currency]
    word_candidate = _amount_word_candidate(text, currency)
    if len(unique_labeled) == 1:
        if word_candidate is not None and labeled[0]["amount"] != word_candidate["amount"]:
            return None
        return labeled[0]
    if len(unique_labeled) > 1:
        return None

    unique_candidates = {(c["currency"], c["amount"]) for c in candidates}
    if word_candidate is not None:
        if unique_labeled and word_candidate not in labeled:
            return None
        if len(unique_candidates) > 1:
            return None
        if candidates and any(c["amount"] != word_candidate["amount"] for c in candidates):
            return None
        return word_candidate
    if len(unique_labeled) == 1:
        return labeled[0]
    if len(candidates) == 1:
        return candidates[0]
    if any(any(re.search(label, line, re.IGNORECASE) for label in labels) for line in str(text or "").splitlines()):
        if candidates and all(candidate["currency"] == currency for candidate in candidates):
            return candidates[-1]
        raw_numbers = re.findall(r"(?<![A-Za-z])\d[\d.,]*(?![A-Za-z])", str(text or ""))
        if raw_numbers and currency:
            amount = parse_amount(raw_numbers[-1], currency)
            if amount is not None:
                return {"amount": amount, "currency": currency}
    return None


def extract_amount_from_text(text: str, expected_currency: str | None = None):
    """Extract one validated amount using all supported document labels."""
    currency = str(expected_currency or "").strip().upper()
    return _extract_amount_from_text(text, currency, _GENERIC_LABELS)


def document_family(category: str | None, description: str | None) -> str:
    """Infer a document family from event semantics, never from an event ID."""
    text = f"{category or ''} {description or ''}".lower()
    if any(token in text for token in ("salary", "payroll", "wage", "net pay")):
        return "salary"
    if str(category or "").strip().lower() in {"groceries", "dining", "shopping", "transport"}:
        return "receipt"
    if any(token in text for token in ("bill", "invoice", "rent", "due", "outstanding", "maintenance")):
        return "bill"
    if any(token in text for token in ("received", "confirmation", "transferred", "transfer")):
        return "payment_confirmation"
    return "receipt"


def extract_amount_for_event(text: str, expected_currency: str | None, category: str | None, description: str | None):
    currency = str(expected_currency or "").strip().upper()
    if not str(category or "").strip() and not str(description or "").strip():
        return _extract_amount_from_text(text, currency, _GENERIC_LABELS)
    family = document_family(category, description)
    for labels in _FAMILY_LABEL_GROUPS[family]:
        if any(
            any(re.search(label, line, re.IGNORECASE) for label in labels)
            for line in str(text or "").splitlines()
        ):
            parsed = _extract_amount_from_text(text, currency, labels)
            if parsed is not None:
                return parsed
    return _extract_amount_from_text(text, currency, _GENERIC_LABELS)


def preprocess_image(image):
    """Make OCR input legible without changing or losing the raw source path."""
    from PIL import Image, ImageOps

    if isinstance(image, (str, Path)):
        with Image.open(image) as raw_image:
            return preprocess_image(raw_image)
    gray = image.convert("L")
    normalized = ImageOps.autocontrast(gray)
    max_dimension = 2400
    scale = min(2.0, max_dimension / max(normalized.size))
    if scale > 1:
        normalized = normalized.resize(
            (round(normalized.width * scale), round(normalized.height * scale)),
            resample=getattr(Image, "Resampling", Image).LANCZOS,
        )
    return normalized


def read_image_text(image_path: Path) -> str:
    """Read OCR text from one PNG after runtime Tesseract configuration."""
    import pytesseract
    from PIL import Image

    with Image.open(image_path) as raw_image:
        return pytesseract.image_to_string(preprocess_image(raw_image))


def _registry_tesseract_paths() -> list[Path]:
    """Find Windows installs registered by the Tesseract installer."""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    roots = (
        winreg.HKEY_LOCAL_MACHINE,
        winreg.HKEY_CURRENT_USER,
    )
    paths: list[Path] = []
    for root in roots:
        try:
            with winreg.OpenKey(root, r"Software\Microsoft\Windows\CurrentVersion\Uninstall") as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        sub_name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, sub_name) as subkey:
                            display, _ = winreg.QueryValueEx(subkey, "DisplayName")
                            try:
                                install, _ = winreg.QueryValueEx(subkey, "InstallLocation")
                            except OSError:
                                install = ""
                            if not install:
                                try:
                                    uninstall, _ = winreg.QueryValueEx(subkey, "UninstallString")
                                    install = str(_install_dir_from_uninstall(uninstall))
                                except OSError:
                                    install = ""
                        if "tesseract" in str(display).lower() and install:
                            paths.append(Path(str(install)) / "tesseract.exe")
                    except (OSError, FileNotFoundError):
                        continue
        except (OSError, FileNotFoundError):
            continue
    return paths


def _drive_tesseract_paths() -> list[Path]:
    """Find common per-drive Windows installs without assuming one drive letter."""
    if os.name != "nt":
        return []
    return [
        Path(f"{drive}:/Tesseract_OCR/tesseract.exe")
        for drive in string.ascii_uppercase
    ] + [
        Path(f"{drive}:/Tesseract-OCR/tesseract.exe")
        for drive in string.ascii_uppercase
    ]


def _install_dir_from_uninstall(command: str) -> Path:
    """Extract the executable directory from a Windows uninstall command."""
    text = str(command or "").strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        executable = text[1:end] if end > 1 else text[1:]
    else:
        executable = text.split(maxsplit=1)[0] if text else ""
    return Path(executable).parent


def resolve_tesseract_path() -> Path | None:
    """Resolve a usable native Tesseract executable without hardcoded paths."""
    configured = os.environ.get("TESSERACT_CMD", "").strip()
    candidates = [Path(configured)] if configured else []
    found = shutil.which("tesseract")
    if found:
        candidates.append(Path(found))
    program_files = os.environ.get("ProgramFiles", "").strip()
    if program_files:
        candidates.append(Path(program_files) / "Tesseract-OCR" / "tesseract.exe")
    candidates.extend(_registry_tesseract_paths())
    candidates.extend(_drive_tesseract_paths())
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def configure_tesseract() -> Path | None:
    """Configure pytesseract when both wrapper and native engine are available."""
    path = resolve_tesseract_path()
    if path is None:
        return None
    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = str(path)
    except ImportError:
        return None
    return path


def verdict_for(binary_present: bool) -> dict:
    if binary_present:
        return {"path": "tess", "flag": None, "amount": None}
    return {"path": "reserve", "flag": "OCR_UNAVAILABLE", "amount": None}


def scan_images(image_dir: Path, binary_present: bool) -> list[dict]:
    """Inventory every PNG under image_dir with its routing verdict.

    With no Tesseract binary every image is honestly attributed to the reserve
    path (flag OCR_UNAVAILABLE, no extracted amount) rather than silently
    dropped or worse, ascribed a fabricated figure.
    """
    rows = []
    for p in sorted(Path(image_dir).glob("image_*.png")):
        v = verdict_for(binary_present)
        rows.append({"image_id": p.stem, **v})
    return rows
