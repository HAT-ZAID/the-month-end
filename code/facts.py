"""Typed evidence facts extracted from ``dataset/messages.csv`` (deterministic, offline).

Messages are untrusted evidence. This module records only what a message states
in a recognised template and never invents money:

- a confirmation without a figure stays ``amount=None``;
- pending bonuses, commissions, prizes, refunds and gig payouts never become cash;
- internal transfers net to zero and never appear as income;
- investment valuation moves are non-cash;
- a one-off arrears/bonus adjustment never becomes recurring salary;
- ended employment/contract produces an ``ended`` change plus a flag, so the
  forecast layer (Task 3) can stop projecting historical income;
- messages that carry embedded instructions are dropped as untrusted.

``parse_messages`` (``code/parser.py``) remains the entry point; this module holds
the extraction rules and the typed objects it returns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from code.amounts import extract_amounts
from code.reconcile import amendment_from_message, negation_before

# Documented vocabularies (brief §Deliverable 1). Kept as data so Task 3 and the
# schema/verifier tasks can validate facts without re-deriving the strings.
SALARY_CHANGE_KINDS = frozenset(
    {"raise", "reduction", "temporary", "first", "regular", "confirmation", "ended", "final"}
)
INCOME_FACT_KINDS = frozenset(
    {"invoice", "prize", "refund", "gig_payout", "investment_sale", "arrears", "bonus", "commission"}
)
INCOME_FACT_STATUSES = frozenset({"confirmed", "pending", "settled"})
FLAG_NAMES = frozenset(
    {
        "employment_ended",
        "contract_ended",
        "seasonal_contract_ended",
        "partial_income_ended",
        "internal_transfer",
        "investment_valuation",
        "refund_pending",
        "prize_pending",
        "bonus_pending",
        "commission_pending",
        "arrears_one_time",
    }
)


def _assert_known(kind: str, allowed: frozenset, flag: str, message_id: str, warnings: list) -> None:
    """Record an unknown vocabulary member instead of silently trusting it."""
    if kind not in allowed:
        warnings.append(
            {"message_id": message_id, "flag": flag, "detail": f"unknown kind {kind!r}"}
        )


@dataclass(frozen=True)
class SalaryChange:
    """One stated change to salary income.

    ``kind`` is one of :data:`SALARY_CHANGE_KINDS`. ``amount`` stays ``None`` when
    the message states no figure. ``confirmed`` is true when a counterparty
    (employer payroll) asserts the figure, false for proposed/pending figures.
    """

    kind: str
    amount: Decimal | None
    currency: str
    effective_date: str | None
    settlement_date: str | None
    confirmed: bool
    source_message_id: str
    scope: str = "full"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "amount": self.amount,
            "currency": self.currency,
            "effective_date": self.effective_date,
            "settlement_date": self.settlement_date,
            "confirmed": self.confirmed,
            "source_message_id": self.source_message_id,
            "scope": self.scope,
        }


@dataclass(frozen=True)
class IncomeFact:
    """A stated income/credit fact. ``status`` is the message-level state."""

    kind: str
    amount: Decimal | None
    currency: str
    date: str | None
    status: str
    source_message_id: str

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "amount": self.amount,
            "currency": self.currency,
            "date": self.date,
            "status": self.status,
            "source_message_id": self.source_message_id,
        }


@dataclass(frozen=True)
class ExpenseFact:
    """A stated recurring-expense change (rent, childcare, subscription)."""

    category: str
    kind: str
    amount: Decimal | None
    currency: str
    effective_date: str | None
    percent_change: Decimal | None
    source_message_id: str

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "kind": self.kind,
            "amount": self.amount,
            "currency": self.currency,
            "effective_date": self.effective_date,
            "percent_change": self.percent_change,
            "source_message_id": self.source_message_id,
        }


@dataclass
class EvidenceFacts:
    """Typed container the kernel and Task 3 forecast policy consume."""

    salary_changes: list[SalaryChange] = field(default_factory=list)
    income_facts: list[IncomeFact] = field(default_factory=list)
    expense_facts: list[ExpenseFact] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    event_updates: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)

    def has_flag(self, name: str) -> bool:
        return name in self.flags

    @property
    def employment_ended(self) -> bool:
        """True when no further historical income may be projected for the user."""
        return bool(
            self.flags & {"employment_ended", "contract_ended", "seasonal_contract_ended"}
        )

    @property
    def salary_updates(self) -> list[dict]:
        """Legacy 2024-era view: dated salary amounts only."""
        return [
            {
                "effective_date": change.effective_date,
                "amount": change.amount,
                "currency": change.currency,
            }
            for change in self.salary_changes
            if change.amount is not None and change.effective_date
        ]

    def to_dict(self) -> dict:
        return {
            "salary_updates": self.salary_updates,
            "salary_changes": [change.to_dict() for change in self.salary_changes],
            "income_facts": [fact.to_dict() for fact in self.income_facts],
            "expense_facts": [fact.to_dict() for fact in self.expense_facts],
            "flags": set(self.flags),
            "event_updates": [dict(update) for update in self.event_updates],
            "warnings": [dict(warning) for warning in self.warnings],
            "facts": self,
        }


# --------------------------------------------------------------------------
# Cue lexicon. Every rule below is an explicit template cue found in
# dataset/messages.csv (English + Indonesian); free prose is never guessed at.
# --------------------------------------------------------------------------

_UNTRUSTED_INSTRUCTION = re.compile(
    r"\bignore (?:all |any |the )?(?:previous|prior|above|earlier)\b"
    r"|\bdisregard (?:all|any|the|previous|prior)\b"
    r"|\bsystem prompt\b|\byou are now\b|\bnew instructions?\b"
    r"|\b(?:approve|authori[sz]e|confirm) (?:this|the) (?:payment|request|transaction|purchase)\b"
    r"|\boverride (?:the|all)\b|\bmark (?:this|it) as\b|\bdo not (?:tell|check)\b"
    r"|\babaikan (?:semua |instruksi )?(?:perintah|instruksi|pesan)\b|\binstruksi sistem\b",
    re.IGNORECASE,
)

_SALARY_WORD = re.compile(r"\b(salary|gaji)\b", re.IGNORECASE)
_INDONESIAN = re.compile(r"\bgaji\b|\brupiah\b|\bdi\b[^.]{0,20}\banda\b", re.IGNORECASE)
_NON_SALARY_MONEY = re.compile(
    r"bonus|commission|refund|reimbursement|lottery|prize|investment|arrears"
    r"|komisi|hadiah|pengembalian dana|tunggakan|investasi|hasil penjualan",
    re.IGNORECASE,
)

_EMPLOYMENT_ENDED = re.compile(
    r"employment has ended|hubungan kerja anda telah berakhir", re.IGNORECASE
)
_CONTRACT_ENDED = re.compile(
    r"seasonal contract has ended|kontrak musiman saat ini telah berakhir", re.IGNORECASE
)
_HOUSEHOLD_ENDED = re.compile(
    r"(?:household|employment|income) record has ended"
    r"|sumber pendapatan kerja rumah tangga telah berakhir",
    re.IGNORECASE,
)
_TEMPORARY = re.compile(
    r"temporary monthly pay|temporary pay|gaji bulanan sementara|gaji sementara", re.IGNORECASE
)
_FIRST_SALARY = re.compile(r"first salary|gaji pertama", re.IGNORECASE)
_REDUCTION = re.compile(
    r"reduced to|dikurangi menjadi|turun menjadi|dipotong menjadi", re.IGNORECASE
)
_RAISE = re.compile(r"increased to|naik menjadi|naik ke|raised to", re.IGNORECASE)
_RESUME = re.compile(
    r"salary of .{0,60}\bresumes\b|gaji rutin anda untuk penggajian berikutnya adalah",
    re.IGNORECASE,
)
_CONFIRMATION = re.compile(
    r"confirmed (?:base )?salary|salary .{0,60}is confirmed"
    r"|gaji pokok yang dikonfirmasi|gaji rutin .{0,60}dikonfirmasi|\bdikonfirmasi\b",
    re.IGNORECASE,
)
_FINAL_PAYMENT = re.compile(r"final (?:settlement|payment) of", re.IGNORECASE)
_REMAINING_SALARY = re.compile(
    r"remaining confirmed monthly salary is|sisa gaji bulanan yang dikonfirmasi adalah",
    re.IGNORECASE,
)

_EFFECTIVE_CUE = re.compile(
    r"change applies from|applies from|berlaku mulai|effective (?:from|on)", re.IGNORECASE
)
_CREDIT_CUE = re.compile(
    r"confirmed credit date is|is scheduled for|scheduled for|confirmed for|expected on"
    r"|dijadwalkan pada|pembayaran sudah dikonfirmasi untuk"
    r"|tanggal kredit yang dikonfirmasi adalah|resumes on",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

_PENDING_WORDING = re.compile(
    r"still (?:subject|pending|awaiting|waiting|processing|being|outstanding)"
    r"|not (?:yet )?(?:been )?(?:approved|credited|confirmed)"
    r"|has not been approved|have not been approved"
    r"|belum (?:disetujui|masuk|dikonfirmasi|diverifikasi)|masih menunggu|masih dalam",
    re.IGNORECASE,
)
_BONUS = re.compile(r"\bbonus\b", re.IGNORECASE)
_COMMISSION = re.compile(r"\bcommission\b|\bkomisi\b", re.IGNORECASE)
_PRIZE_PENDING = re.compile(
    r"prize claim has been verified and is still in payment processing"
    r"|selected for a cash prize|terpilih untuk menerima hadiah"
    r"|klaim hadiah .{0,60}masih dalam proses",
    re.IGNORECASE,
)
_PRIZE_SETTLED = re.compile(
    r"prize proceeds have reached your account|hadiah .{0,40}sudah (?:diterima|masuk)",
    re.IGNORECASE,
)
_REFUND_PENDING = re.compile(
    r"refund has been initiated but has not reached"
    r"|pengembalian dana sudah diproses, tetapi belum masuk"
    r"|foreign-currency refund is still processing",
    re.IGNORECASE,
)
_REFUND_SETTLED = re.compile(
    r"reimbursement for your earlier work expense|penggantian atas biaya kerja",
    re.IGNORECASE,
)
_GIG_PENDING = re.compile(
    r"payout is still pending|pembayaran berikutnya dari .{0,20}masih tertunda"
    r"|belum dapat ditarik",
    re.IGNORECASE,
)
_INVESTMENT_SALE_SETTLED = re.compile(
    r"proceeds from your investment sale have settled"
    r"|hasil penjualan investasi anda sudah masuk",
    re.IGNORECASE,
)
_INVESTMENT_VALUATION = re.compile(
    r"displayed (?:market )?value|nilai investasi yang ditampilkan", re.IGNORECASE
)
_INTERNAL_TRANSFER = re.compile(
    r"transfer between your two accounts|transfer antara dua rekening", re.IGNORECASE
)
_INVOICE_CONFIRMED = re.compile(
    r"client approved an invoice payment of"
    r"|klien menyetujui pembayaran faktur sebesar",
    re.IGNORECASE,
)
_INVOICE_DATE_CUE = re.compile(
    r"settlement is expected on|penyelesaian diperkirakan pada", re.IGNORECASE
)
_ARREARS = re.compile(
    r"one-time arrears adjustment of|penyesuaian tunggakan satu kali sebesar", re.IGNORECASE
)
_RENT_PERCENT = re.compile(
    r"(?:renewed lease increases monthly rent by"
    r"|perpanjangan sewa menaikkan biaya sewa bulanan sebesar)\s*(\d+(?:[.,]\d+)?)\s*%",
    re.IGNORECASE,
)
_RENT_APPLIES = re.compile(r"applies from|berlaku mulai", re.IGNORECASE)
_CHILDCARE = re.compile(
    r"new recurring childcare payment begins|pembayaran penitipan anak", re.IGNORECASE
)
_SUBSCRIPTION_CHANGE = re.compile(
    r"(?:subscription|membership)\s+(?:fee|price|cost|plan)?\s*(?:has\s+|is\s+)?"
    r"(?:increased|changed|risen)\s+to",
    re.IGNORECASE,
)


_SENTENCE_END = re.compile(r"\.(?=\s|$)|;|\n")


def _segment_after(text: str, cue: re.Pattern[str], limit: int = 120) -> str:
    """Return the sentence fragment right after ``cue`` (empty when absent).

    Stops at a sentence end, not at any dot, so a decimal point such as
    ``1422.85`` survives intact.
    """
    match = cue.search(text)
    if not match:
        return ""
    tail = text[match.end() : match.end() + limit]
    stop = _SENTENCE_END.search(tail)
    if stop:
        tail = tail[: stop.start()]
    return tail


def _amount_after(text: str, cue: re.Pattern[str], lang: str = "") -> tuple[Decimal | None, str]:
    """First currency-coded amount stated directly after ``cue``."""
    segment = _segment_after(text, cue)
    if not segment:
        return None, ""
    found = extract_amounts(segment, lang)
    if not found:
        return None, ""
    return found[0]["amount"], found[0]["currency"]


def _date_after(text: str, cue: re.Pattern[str]) -> str | None:
    match = cue.search(text)
    if not match:
        return None
    tail = text[match.end() : match.end() + 80]
    date_match = _ISO_DATE.search(tail)
    return date_match.group(1) if date_match else None


# Cues that introduce a distinct salary fact inside one message. A fact's own
# region stops where the next fact starts, so a date can never bleed across.
_FACT_SPLIT_CUES = (_REMAINING_SALARY, _FINAL_PAYMENT)

# Directly attached payment wording ("The final settlement of USD 500 will be paid
# on 2026-03-20"). Only consulted inside a fact's own region.
_PAYMENT_DATE_CUE = re.compile(
    r"will be paid on|paid on|settlement on|dibayarkan pada", re.IGNORECASE
)


def _fact_date(text: str, cue: re.Pattern[str]) -> str | None:
    """Date that belongs to the single fact introduced by ``cue``.

    The fact region runs from the cue to the next competing fact cue or sentence
    end, whichever comes first, and the date must be introduced by a date cue
    inside that region. A fact without its own dated cue keeps ``None``; it never
    borrows a neighbouring fact's date.
    """
    match = cue.search(text)
    if not match:
        return None
    end = len(text)
    sentence_end = _SENTENCE_END.search(text, match.end())
    if sentence_end:
        end = sentence_end.start()
    for other_cue in _FACT_SPLIT_CUES:
        other = other_cue.search(text, match.end())
        if other:
            end = min(end, other.start())
    region = text[match.end() : end]
    cue_matches = []
    for date_cue in (_CREDIT_CUE, _EFFECTIVE_CUE, _PAYMENT_DATE_CUE):
        cue_matches.extend(date_cue.finditer(region))
    ordered = sorted(cue_matches, key=lambda found: found.start())
    for index, candidate in enumerate(ordered):
        # A negated date cue ("will not be paid on 2026-03-20") is not a date for
        # this fact; keep looking for the first affirmative one.
        if negation_before(region, candidate.start()):
            continue
        # Only look up to the next cue: a later cue's date belongs to that cue,
        # never to this one.
        stop = ordered[index + 1].start() if index + 1 < len(ordered) else len(region)
        date_match = _ISO_DATE.search(region[candidate.end() : stop])
        if date_match:
            return date_match.group(1)
    return None


def _lang(text: str) -> str:
    return "id" if _INDONESIAN.search(text) else ""


def _salary_kind(text: str) -> str | None:
    if _EMPLOYMENT_ENDED.search(text) or _CONTRACT_ENDED.search(text) or _HOUSEHOLD_ENDED.search(text):
        return "ended"
    if _FINAL_PAYMENT.search(text):
        return "final"
    if _TEMPORARY.search(text):
        return "temporary"
    if _FIRST_SALARY.search(text):
        return "first"
    if _REDUCTION.search(text):
        return "reduction"
    if _RAISE.search(text):
        return "raise"
    if _RESUME.search(text):
        return "regular"
    if _CONFIRMATION.search(text):
        return "confirmation"
    return None


def _salary_amount(text: str, kind: str, lang: str) -> tuple[Decimal | None, str]:
    cues = {
        "raise": _RAISE,
        "reduction": _REDUCTION,
        "temporary": re.compile(
            r"temporary monthly pay is|temporary pay is"
            r"|gaji bulanan sementara (?:anda )?adalah|gaji sementara (?:anda )?adalah",
            re.IGNORECASE,
        ),
        "first": re.compile(
            r"first salary (?:will be|from the new employer is|of)"
            r"|gaji pertama (?:anda )?(?:sebesar|dari perusahaan baru adalah|adalah)",
            re.IGNORECASE,
        ),
        "regular": re.compile(r"regular salary of", re.IGNORECASE),
        "confirmation": re.compile(
            r"confirmed (?:base )?salary is|salary of|gaji pokok yang dikonfirmasi adalah"
            r"|gaji rutin anda untuk penggajian berikutnya adalah",
            re.IGNORECASE,
        ),
        "final": _FINAL_PAYMENT,
    }
    cue = cues.get(kind)
    if cue is None:
        return None, ""
    return _amount_after(text, cue, lang)


def _extract_salary_changes(text: str, message_id: str) -> list[SalaryChange]:
    """Salary/stipend statements. At most one change per message, plus end signals."""
    lang = _lang(text)
    end_scope = ""
    if _HOUSEHOLD_ENDED.search(text):
        end_scope = "partial"
    elif _EMPLOYMENT_ENDED.search(text) or _CONTRACT_ENDED.search(text):
        end_scope = "full"
    if end_scope:
        # An ended income source is a stop signal, never a payment.
        changes = [
            SalaryChange(
                kind="ended",
                amount=None,
                currency="",
                effective_date=None,
                settlement_date=None,
                confirmed=True,
                source_message_id=message_id,
                scope=end_scope,
            )
        ]
        remaining_amount, remaining_currency = _amount_after(text, _REMAINING_SALARY, lang)
        if remaining_amount is not None:
            changes.append(
                SalaryChange(
                    kind="confirmation",
                    amount=remaining_amount,
                    currency=remaining_currency or "",
                    effective_date=None,
                    settlement_date=_fact_date(text, _REMAINING_SALARY),
                    confirmed=True,
                    source_message_id=message_id,
                    scope="remaining",
                )
            )
        final_amount, final_currency = _amount_after(text, _FINAL_PAYMENT, lang)
        if final_amount is not None:
            changes.append(
                SalaryChange(
                    kind="final",
                    amount=final_amount,
                    currency=final_currency or "",
                    effective_date=None,
                    settlement_date=_fact_date(text, _FINAL_PAYMENT),
                    confirmed=True,
                    source_message_id=message_id,
                    scope="final",
                )
            )
        return changes

    if not _SALARY_WORD.search(text):
        return []
    if _NON_SALARY_MONEY.search(text):
        # Bonus/commission/refund/arrears/investment wording: not salary income.
        return []
    kind = _salary_kind(text)
    if kind is None:
        return []
    amount, currency = _salary_amount(text, kind, lang)
    effective = _date_after(text, _EFFECTIVE_CUE)
    credit = _date_after(text, _CREDIT_CUE)
    return [
        SalaryChange(
            kind=kind,
            amount=amount,
            currency=currency or "",
            effective_date=effective,
            settlement_date=None if effective else credit,
            confirmed=True,
            source_message_id=message_id,
            scope="full",
        )
    ]


def _extract_income_facts(text: str, message_id: str) -> list[IncomeFact]:
    """Confirmed (cash) vs pending (not cash) credits."""
    lang = _lang(text)
    facts: list[IncomeFact] = []

    if _INVOICE_CONFIRMED.search(text):
        amount, currency = _amount_after(text, _INVOICE_CONFIRMED, lang)
        facts.append(
            IncomeFact(
                kind="invoice",
                amount=amount,
                currency=currency,
                date=_date_after(text, _INVOICE_DATE_CUE),
                status="confirmed",
                source_message_id=message_id,
            )
        )
    if _PRIZE_SETTLED.search(text):
        facts.append(
            IncomeFact("prize", None, "", None, "settled", message_id)
        )
    if _PRIZE_PENDING.search(text):
        facts.append(IncomeFact("prize", None, "", None, "pending", message_id))
    if _REFUND_SETTLED.search(text):
        facts.append(IncomeFact("refund", None, "", None, "settled", message_id))
    if _REFUND_PENDING.search(text):
        facts.append(IncomeFact("refund", None, "", None, "pending", message_id))
    if _GIG_PENDING.search(text):
        facts.append(IncomeFact("gig_payout", None, "", None, "pending", message_id))
    if _INVESTMENT_SALE_SETTLED.search(text):
        facts.append(IncomeFact("investment_sale", None, "", None, "settled", message_id))
    if _ARREARS.search(text):
        amount, currency = _amount_after(text, _ARREARS, lang)
        facts.append(IncomeFact("arrears", amount, currency, None, "confirmed", message_id))
    if _BONUS.search(text) and _PENDING_WORDING.search(text):
        facts.append(IncomeFact("bonus", None, "", None, "pending", message_id))
    if _COMMISSION.search(text) and _PENDING_WORDING.search(text):
        facts.append(IncomeFact("commission", None, "", None, "pending", message_id))
    return facts


def _extract_expense_facts(text: str, message_id: str) -> list[ExpenseFact]:
    """Recurring-expense changes, only when the message states the change."""
    lang = _lang(text)
    facts: list[ExpenseFact] = []

    rent = _RENT_PERCENT.search(text)
    if rent:
        raw = rent.group(1).replace(",", ".")
        try:
            percent = Decimal(raw)
        except Exception:
            percent = None
        facts.append(
            ExpenseFact(
                category="rent",
                kind="increase",
                amount=None,
                currency="",
                effective_date=_date_after(text, _RENT_APPLIES),
                percent_change=percent,
                source_message_id=message_id,
            )
        )
    if _CHILDCARE.search(text):
        facts.append(
            ExpenseFact(
                category="childcare",
                kind="new_recurring",
                amount=None,
                currency="",
                effective_date=_date_after(text, _CREDIT_CUE),
                percent_change=None,
                source_message_id=message_id,
            )
        )
    if _SUBSCRIPTION_CHANGE.search(text):
        amount, currency = _amount_after(text, _SUBSCRIPTION_CHANGE, lang)
        if amount is not None:
            facts.append(
                ExpenseFact(
                    category="subscription",
                    kind="amount_change",
                    amount=amount,
                    currency=currency,
                    effective_date=_date_after(text, _EFFECTIVE_CUE),
                    percent_change=None,
                    source_message_id=message_id,
                )
            )
    return facts


def _flags_for(text: str) -> set[str]:
    flags: set[str] = set()
    if _EMPLOYMENT_ENDED.search(text):
        flags.add("employment_ended")
    if _CONTRACT_ENDED.search(text):
        flags.add("contract_ended")
        flags.add("seasonal_contract_ended")
    if _HOUSEHOLD_ENDED.search(text):
        flags.add("partial_income_ended")
    if _INTERNAL_TRANSFER.search(text):
        flags.add("internal_transfer")
    if _INVESTMENT_VALUATION.search(text):
        flags.add("investment_valuation")
    if _REFUND_PENDING.search(text):
        flags.add("refund_pending")
    if _PRIZE_PENDING.search(text):
        flags.add("prize_pending")
    if _BONUS.search(text) and _PENDING_WORDING.search(text):
        flags.add("bonus_pending")
    if _COMMISSION.search(text) and _PENDING_WORDING.search(text):
        flags.add("commission_pending")
    if _ARREARS.search(text):
        flags.add("arrears_one_time")
    return flags


def extract_facts(messages: list[dict]) -> EvidenceFacts:
    """Extract typed facts from user-scoped message rows (dict-like rows)."""
    facts = EvidenceFacts()
    for message in messages:
        raw_text = message.get("message_text")
        text = "" if raw_text is None else str(raw_text)
        message_id = str(message.get("message_id") or "").strip()
        if not text:
            continue
        if _UNTRUSTED_INSTRUCTION.search(text):
            # Embedded instructions are data, never commands: drop the message
            # rather than let it steer any extracted fact.
            facts.warnings.append(
                {
                    "message_id": message_id,
                    "flag": "UNTRUSTED_INSTRUCTION",
                    "detail": "message embedded instructions; content ignored",
                }
            )
            continue

        flags = _flags_for(text)
        salary_changes = _extract_salary_changes(text, message_id)
        income_facts = _extract_income_facts(text, message_id)
        for change in salary_changes:
            _assert_known(
                change.kind, SALARY_CHANGE_KINDS, "UNKNOWN_SALARY_KIND", message_id, facts.warnings
            )
        for income in income_facts:
            _assert_known(
                income.kind, INCOME_FACT_KINDS, "UNKNOWN_INCOME_KIND", message_id, facts.warnings
            )
            _assert_known(
                income.status,
                INCOME_FACT_STATUSES,
                "UNKNOWN_INCOME_STATUS",
                message_id,
                facts.warnings,
            )
        for flag in flags:
            _assert_known(flag, FLAG_NAMES, "UNKNOWN_FLAG", message_id, facts.warnings)

        facts.flags |= flags
        facts.salary_changes.extend(salary_changes)
        facts.income_facts.extend(income_facts)
        facts.expense_facts.extend(_extract_expense_facts(text, message_id))
        update = amendment_from_message(message)
        if update is not None:
            facts.event_updates.append(update)
    return facts
