"""The Textual interface: what the product actually looks like.

Why this file exists: the Rich screens printed panels that wrapped, spilled and
lost their alignment the moment a description ran long, and the home screen was a
census of the calibration fixture.  A terminal does not have to look like that.
Textual gives real layout, real clipping and real colour, and its widgets never
widen a column to fit a stray sentence.

The rule the layout enforces: **this module renders and computes nothing about
money.**  Every figure arrives from :mod:`code.service`, which takes it from
:func:`code.solver.position` and :func:`code.solver.solve_request`.  There is no
arithmetic over amounts in this file, and there should never be.

Run it with ``python -m tools.app``.  The Rich interface (``python -m tools.tui``)
stays as the fallback for a machine without Textual.
"""
from __future__ import annotations

import sys
from datetime import date
from functools import partial
from pathlib import Path

try:  # Textual is optional: the Rich TUI must keep working without it.
    from textual import on
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.screen import ModalScreen, Screen
    from textual.widgets import (
        Button,
        DataTable,
        Footer,
        Header,
        Input,
        Label,
        RichLog,
        Select,
        Static,
        TabbedContent,
        TabPane,
    )
    from textual.worker import Worker, WorkerState
except ImportError:  # pragma: no cover - exercised only without the package
    sys.stderr.write(
        "textual is not installed, so the modern interface cannot start.\n"
        "Install it with:  pip install textual\n"
        "The Rich interface still works:  python -m tools.tui\n"
    )
    raise SystemExit(2)

if __package__ in (None, ""):  # ``python tools/app.py``
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.text import Text

from code import models as model_registry
from code import paths, service

GREEN = "#34d399"
AMBER = "#fbbf24"
RED = "#f87171"
BLUE = "#7dd3fc"
MUTED = "#64748b"
TEXT = "#e2e8f0"

STATUS_COLOUR = {
    "affordable_now": GREEN,
    "affordable_with_plan": AMBER,
    "affordable_later": AMBER,
    "not_affordable": RED,
}


def _cell(value, style: str = TEXT) -> Text:
    return Text("" if value is None else str(value), style=style)


def _money(value) -> str:
    return service.money(value)


def run_summary_line(summary: dict) -> str:
    """One dim line describing a chat turn: what ran and whether it was grounded.

    Derived only from ``chat.ask``'s ``run_summary`` (Task 22), itself derived
    from :class:`code.chat.ChatRunState`: the line repeats no figure of its own
    and never claims evidence the turn did not produce.
    """
    summary = summary or {}
    tools = ", ".join(str(name) for name in (summary.get("tools") or [])) or "no tools"
    parts = [f"tools: {tools}"]
    parts.append(
        "kernel result" if summary.get("kernel_result") else "no kernel result"
    )
    parts.append(
        "evidence found" if summary.get("evidence_found") else "no evidence"
    )
    reason = summary.get("fallback_reason")
    if reason:
        parts.append(f"fallback: {reason}")
    parts.append(f"{int(summary.get('elapsed_ms') or 0)} ms")
    return " · ".join(parts)


#: What "yes" looks like typed into the form's monthly-pay question.
MONTHLY_ANSWERS = frozenset({"monthly", "yes", "y", "true"})

#: The one chat worker group.  Exclusive means a new question cancels the turn
#: it replaces instead of leaving two provider turns racing for the same log.
ASK_GROUP = "chat-ask"


def fact_payload(kind, amount, *, when="", description="", category="",
                 recurrence="") -> dict:
    """The Ledger form's raw strings, ready for :func:`code.service.add_fact`.

    The form used to send only kind, amount, date and description, and the
    forecast could not see the row it created (CR-05).  Two words decide that:
    the *category* a bill lands on, and whether income is *monthly* pay -- the
    service calls that ``salary`` and only then projects it forward.

    A bill or subscription with no category is refused here, before anything is
    stored, because guessing a bucket for someone's committed money is worse
    than asking.  The refusal names the categories the forecast can read, so the
    person can correct the entry in place.
    """
    key = str(kind or "").strip().lower()
    if key not in service.FACT_KINDS:
        raise service.EngineError(
            f"unknown kind {kind!r}; use one of "
            f"{', '.join(sorted(service.FACT_KINDS))}"
        )
    stated = str(category or "").strip()
    if key in ("bill", "subscription") and not stated:
        allowed = ", ".join(sorted(service.PROJECTED_EXPENSE_CATEGORIES))
        raise service.EngineError(
            f"a {key} needs a category the forecast projects; use one of {allowed}"
        )
    answer = str(recurrence or "").strip().lower()
    if key == "income":
        # Exactly one question is asked of income: is this monthly pay?  A
        # conflicting answer is treated as "not monthly", never as a guess.
        answer = "monthly" if answer in MONTHLY_ANSWERS else ""
    return {
        "kind": key,
        "amount": str(amount or "").strip(),
        "when": str(when or "").strip() or None,
        "description": str(description or "").strip(),
        "category": stated,
        "recurrence": answer,
    }


class Metric(Vertical):
    """A labelled figure.  The value is always a kernel figure, never a sum."""

    def __init__(self, label: str, value: str = "", note: str = "",
                 style: str = TEXT, **kwargs):
        super().__init__(**kwargs)
        self._label = label
        self._value = value
        self._note = note
        self._style = style

    def compose(self) -> ComposeResult:
        yield Label(self._label, classes="metric-label")
        yield Static(self._value, classes="metric-value")
        yield Static(self._note, classes="metric-note")

    def update(self, value: str, note: str = "", style: str = TEXT) -> None:
        self._value = value
        self._style = style
        self.query_one(".metric-value", Static).update(Text(value, style=f"bold {style}"))
        if note:
            self.query_one(".metric-note", Static).update(Text(note, style=MUTED))

    @property
    def value(self) -> str:
        """The rendered figure, so a test can read what the screen shows."""
        return self._value


class Onboarding(Screen):
    """Who are you, and what do you have?  One line at a time."""

    BINDINGS = [("escape", "dismiss_quiet", "Back")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with VerticalScroll(id="onboard"):
            yield Static("Buy or Wait?", classes="brand")
            yield Static(
                "A decision assistant for your own money. Nothing here is written to "
                "the calibration fixture; your facts live in your own local account.",
                classes="panel-sub",
            )
            yield Static("Continue as", classes="section")
            yield Vertical(id="known-users")
            yield Static("Or start a new person", classes="section")
            with Horizontal(classes="field-row"):
                yield Label("Name")
                yield Input(placeholder="e.g. Sam", id="ob-name")
            with Horizontal(classes="field-row"):
                yield Label("Currency")
                yield Input(placeholder="e.g. ZAR", id="ob-currency")
            with Horizontal(classes="field-row"):
                yield Label("Balance now")
                yield Input(placeholder="e.g. 12000", id="ob-balance")
            with Horizontal(classes="field-row"):
                yield Label("Keep at least")
                yield Input(placeholder="e.g. 2000", id="ob-floor")
            with Horizontal(classes="actions"):
                yield Button("Start", id="ob-start", variant="primary")
                yield Button("Quit", id="ob-quit")
            yield Static(
                "Your balance and floor are the two facts the kernel cannot invent. "
                "Leave either blank and it stays unknown: the Position tab will name "
                "the fact to fill in before it decides anything. Everything else is "
                "added one movement at a time.",
                classes="note",
            )
        yield Footer()

    def on_mount(self) -> None:
        holder = self.query_one("#known-users", Vertical)
        names = service.list_users()
        if not names:
            holder.mount(Static("No one is stored yet.", classes="note"))
            return
        for name in names:
            holder.mount(Button(f"  {name}", id=f"user-{name}", classes="user-chip"))

    @on(Button.Pressed, ".user-chip")
    def _pick(self, event: Button.Pressed) -> None:
        name = event.button.id.removeprefix("user-")
        account = service.load_user(name)
        if account is None:
            self.notify(f"{name} could not be loaded", severity="error")
            return
        self.app.use_account(account)
        self.app.pop_screen()

    @on(Button.Pressed, "#ob-start")
    def _start(self, event: Button.Pressed) -> None:
        name = self.query_one("#ob-name", Input).value.strip()
        if not name:
            self.notify("A name is needed before anything can be kept", severity="error")
            return
        try:
            account = service.create_user(
                name,
                currency=self.query_one("#ob-currency", Input).value.strip(),
                balance=self.query_one("#ob-balance", Input).value.strip(),
                floor=self.query_one("#ob-floor", Input).value.strip(),
            )
        except Exception as exc:
            self.notify(f"{type(exc).__name__}: {exc}", severity="error")
            return
        self.app.use_account(account)
        self.app.pop_screen()

    @on(Button.Pressed, "#ob-quit")
    def _quit(self, event: Button.Pressed) -> None:
        self.app.exit()

    def action_dismiss_quiet(self) -> None:
        if self.app.account is not None:
            self.app.pop_screen()


class UserPicker(ModalScreen):
    """Switch person, or add one, without leaving the app."""

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Static("People on this machine", classes="section")
            yield Static(
                "Choose one person. Nothing is decided or asked about money "
                "until you do.",
                classes="note",
            )
            yield Vertical(id="picker-users")
            with Horizontal(classes="actions"):
                yield Button("New person", id="picker-new", variant="primary")
                yield Button("Close", id="picker-close")

    def on_mount(self) -> None:
        holder = self.query_one("#picker-users", Vertical)
        names = service.list_users()
        if not names:
            holder.mount(Static("No one is stored yet.", classes="note"))
        for name in names:
            holder.mount(Button(name, id=f"pick-{name}", classes="user-chip"))

    @on(Button.Pressed, ".user-chip")
    def _pick(self, event: Button.Pressed) -> None:
        name = event.button.id.removeprefix("pick-")
        account = service.load_user(name)
        if account is not None:
            self.app.use_account(account)
        self.app.pop_screen()

    @on(Button.Pressed, "#picker-new")
    def _new(self, event: Button.Pressed) -> None:
        self.app.pop_screen()
        self.app.push_screen(Onboarding())

    @on(Button.Pressed, "#picker-close")
    def _close(self, event: Button.Pressed) -> None:
        self.app.pop_screen()


class BuyOrWait(App):
    """The application shell: seven views of one account, never of the fixture."""

    CSS = f"""
    Screen {{ background: #0b1220; color: {TEXT}; }}
    Header {{ background: #0f172a; color: {BLUE}; }}
    Footer {{ background: #0f172a; color: {MUTED}; }}
    TabbedContent {{ height: 1fr; }}
    TabPane {{ padding: 1 2; }}
    .brand {{ color: {BLUE}; text-style: bold; padding-bottom: 1; }}
    .section {{ color: {BLUE}; text-style: bold; padding-top: 1; }}
    .panel-sub {{ color: {MUTED}; }}
    .note {{ color: {MUTED}; text-style: italic; padding-top: 1; }}
    .field-row {{ height: 3; }}
    .field-row Label {{ width: 16; color: #94a3b8; content-align: left middle; }}
    .field-row Input {{ width: 24; }}
    .actions {{ height: 3; }}
    Button {{ min-width: 14; }}
    Select {{ width: 46; }}
    .user-chip {{ width: 40; margin-bottom: 1; }}
    DataTable {{ height: auto; max-height: 18; background: #0f172a; }}
    #metrics {{ layout: grid; grid-size: 3 2; grid-gutter: 1 2; height: auto; }}
    .metric {{ border: round #1f2a37; background: #111c2b; padding: 0 1; height: 5; }}
    .metric-label {{ color: {MUTED}; width: 1fr; }}
    .metric-value {{ text-style: bold; width: 1fr; content-align: right middle; }}
    .metric-note {{ color: {MUTED}; width: 1fr; }}
    #onboard {{ padding: 1 2; }}
    #picker {{ background: #111c2b; border: round {BLUE}; padding: 1 2; width: 60; height: auto; }}
    #pos-head {{ color: {BLUE}; text-style: bold; }}
    #pos-sub {{ color: {MUTED}; padding-bottom: 1; }}
    #pos-missing {{ color: {AMBER}; padding-bottom: 1; }}
    #buy-result {{ padding-top: 1; }}
    #buy-explain {{ color: #cbd5e1; padding-top: 1; }}
    #chat-progress {{ color: {BLUE}; height: auto; padding-top: 1; }}
    RichLog {{ background: #0f172a; border: round #1f2a37; }}
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("escape", "cancel_ask", "Cancel ask"),
        ("ctrl+f", "facts", "Facts used"),
    ]

    account: dict | None = None
    ask_worker: Worker | None = None
    chat_session: object | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="tabs"):
            with TabPane("Position", id="tab-position"):
                with VerticalScroll():
                    with Horizontal():
                        yield Static(id="pos-head")
                        yield Static("", id="pos-who")
                    yield Static(id="pos-sub")
                    yield Static(id="pos-missing")
                    with Horizontal(id="metrics"):
                        yield Metric("Available balance", id="m-balance")
                        yield Metric("Minimum to keep", id="m-floor")
                        yield Metric("Headroom", id="m-headroom")
                        yield Metric("Committed outflows", id="m-committed")
                        yield Metric("Largest safe payment", id="m-safe")
                        yield Metric("Currency", id="m-currency")
                    yield Static(id="pos-assumptions", classes="note")
                    yield Static("Your facts", classes="section")
                    with Horizontal(classes="field-row"):
                        yield Label("Balance now")
                        yield Input(placeholder="e.g. 12000", id="profile-balance")
                    with Horizontal(classes="field-row"):
                        yield Label("Keep at least")
                        yield Input(placeholder="e.g. 2000", id="profile-floor")
                    with Horizontal(classes="actions"):
                        yield Button("Save facts", id="profile-save", variant="primary")
                        yield Button("Switch person", id="switch-person")
                        yield Button("Refresh", id="refresh-position")
                    yield Static(id="profile-status", classes="note")

            with TabPane("Purchase", id="tab-buy"):
                with VerticalScroll():
                    yield Static("Should I buy it?", classes="brand")
                    yield Static(
                        "The kernel answers: pay in full, pay partially, use an "
                        "installment, wait, or do not proceed.",
                        classes="panel-sub",
                    )
                    with Horizontal(classes="field-row"):
                        yield Label("Amount")
                        yield Input(placeholder="e.g. 900", id="buy-amount")
                    with Horizontal(classes="field-row"):
                        yield Label("On date")
                        yield Input(value=date.today().isoformat(), id="buy-on")
                    with Horizontal(classes="field-row"):
                        yield Label("By date")
                        yield Input(placeholder="optional deadline", id="buy-by")
                    with Horizontal(classes="field-row"):
                        yield Label("What is it")
                        yield Input(placeholder="optional description", id="buy-text")
                    with Horizontal(classes="actions"):
                        yield Button("Decide", id="buy-go", variant="primary")
                    yield DataTable(id="buy-table")
                    yield Static(id="buy-explain")

            with TabPane("Ledger", id="tab-ledger"):
                with VerticalScroll():
                    yield Static("What is recorded", classes="brand")
                    yield Static(
                        "One movement at a time. Income, bills, subscriptions and "
                        "refunds write to your account immediately.",
                        classes="panel-sub",
                    )
                    yield DataTable(id="ledger-table")
                    with Horizontal(classes="field-row"):
                        yield Label("Kind")
                        yield Input(placeholder="income / bill / subscription / refund",
                                    id="fact-kind")
                    with Horizontal(classes="field-row"):
                        yield Label("Amount")
                        yield Input(placeholder="e.g. 15000", id="fact-amount")
                    with Horizontal(classes="field-row"):
                        yield Label("Date")
                        yield Input(value=date.today().isoformat(), id="fact-date")
                    with Horizontal(classes="field-row"):
                        yield Label("Description")
                        yield Input(placeholder="e.g. salary", id="fact-text")
                    with Horizontal(classes="field-row"):
                        yield Label("Category")
                        yield Input(placeholder="e.g. rent — required for a bill",
                                    id="fact-category")
                    with Horizontal(classes="field-row"):
                        yield Label("Monthly pay?")
                        yield Input(placeholder="yes / no — is this income a salary?",
                                    id="fact-recurrence")
                    with Horizontal(classes="actions"):
                        yield Button("Add movement", id="fact-add", variant="primary")
                    yield Static(id="fact-status", classes="note")

            with TabPane("Decisions", id="tab-decisions"):
                with VerticalScroll():
                    yield Static("Everything you asked about", classes="brand")
                    yield Static(
                        "Each request is re-decided against the account as it stands "
                        "now by the same kernel the Purchase tab uses.",
                        classes="panel-sub",
                    )
                    yield DataTable(id="decisions-table")

            with TabPane("Report", id="tab-report"):
                with VerticalScroll():
                    yield Static("Calibration report", classes="brand")
                    yield Static(id="report-banner")
                    yield Static(
                        "This tab is the old dashboard. It describes the mock fixture "
                        "the engine was calibrated and tested on. It is not your money "
                        "and no figure here is about you.",
                        classes="note",
                    )
                    yield DataTable(id="report-table")

            with TabPane("Chat", id="tab-chat"):
                with VerticalScroll():
                    yield Static("Talk to it", classes="brand")
                    yield Static(
                        "A model may choose which tool to run. The kernel still owns "
                        "every number; the model can only ask for it.",
                        classes="panel-sub",
                    )
                    yield RichLog(id="chat-log", wrap=True)
                    yield Static("", id="chat-progress")
                    with Horizontal(classes="actions"):
                        yield Button("Facts used", id="chat-facts")
                    with Horizontal(classes="field-row"):
                        yield Label("Model")
                        yield Select([], id="model-select", allow_blank=True)
                    with Horizontal(classes="field-row"):
                        yield Label("Say")
                        yield Input(placeholder="e.g. what can I spend this week?",
                                    id="chat-input")
        yield Footer()

    # ------------------------------------------------------------ lifecycle

    def on_mount(self) -> None:
        widths = {
            "buy-table": (("Field", 24), ("Value", 44)),
            "ledger-table": (("ID", 10), ("Date", 12), ("Kind", 16),
                             ("Description", 38), ("Amount", 14), ("Status", 12)),
            "decisions-table": (("Request", 12), ("On", 12), ("Amount", 14),
                                ("Verdict", 22), ("Method", 22), ("Plan", 36)),
            "report-table": (("Fixture file", 28), ("Rows", 10), ("Path", 42)),
        }
        for table_id, columns in widths.items():
            table = self.query_one(f"#{table_id}", DataTable)
            for label, width in columns:
                table.add_column(label, width=width)
        self.query_one("#pos-head", Static).update("Position")
        self.expand = True
        self.model_choice = model_registry.NO_MODEL
        try:
            entries = model_registry.options()
            select = self.query_one("#model-select", Select)
            select.set_options([(entry["label"], entry["id"]) for entry in entries])
            preferred = next(
                (entry["id"] for entry in entries
                 if entry["privacy"] == "local" and "gemma" in entry["id"].lower()),
                model_registry.NO_MODEL,
            )
            if preferred == model_registry.NO_MODEL:
                preferred = next(
                    (entry["id"] for entry in entries if entry["privacy"] == "local"),
                    model_registry.NO_MODEL,
                )
            select.value = preferred
            self.model_choice = preferred
        except Exception:
            # A model menu must never stop the app from opening.
            self.model_choice = model_registry.NO_MODEL
        names = service.list_users()
        if not names:
            self.push_screen(Onboarding())
            return
        if len(names) > 1:
            # Two people stored and no explicit choice: opening either one would
            # put a stranger's balance under every tab.  Show the app with no
            # person loaded and let the picker answer the question.
            self.refresh_all()
            self.push_screen(UserPicker())
            return
        account = service.load_user(names[0])
        if account is not None:
            self.use_account(account)

    def use_account(self, account: dict) -> None:
        """Make ``account`` the one this screen shows and asks about.

        An answer in flight belongs to the account it was asked about, so
        switching person cancels it rather than letting one ledger's reply
        appear under another ledger's name.
        """
        current = str(((self.account or {}).get("profile") or {}).get("user_id") or "")
        incoming = str((account.get("profile") or {}).get("user_id") or "")
        worker = self.ask_worker
        if incoming != current and worker is not None and not worker.is_finished:
            worker.cancel()
            self.ask_worker = None
            self._set_chat_progress("")
        if incoming != current:
            # A conversation belongs to the person it was had with: switching
            # person starts a new one rather than carrying a stale tool list.
            self.chat_session = None
        self.account = account
        profile = account.get("profile") or {}
        self.sub_title = str(profile.get("user_id") or "")
        self.query_one("#profile-status", Static).update("")
        self.refresh_all()

    def refresh_all(self) -> None:
        self.refresh_position()
        self.refresh_ledger()
        self.refresh_decisions()
        self.refresh_report()
        self.chat_hello()

    # ---------------------------------------------------------------- views

    def refresh_position(self) -> None:
        head = self.query_one("#pos-head", Static)
        who = self.query_one("#pos-who", Static)
        sub = self.query_one("#pos-sub", Static)
        missing = self.query_one("#pos-missing", Static)
        notes = self.query_one("#pos-assumptions", Static)
        if self.account is None:
            head.update("Position")
            who.update("")
            sub.update("No person is loaded yet — choose one under Switch person.")
            missing.update("")
            notes.update("")
            return
        profile = self.account.get("profile") or {}
        name = str(profile.get("user_id") or "")
        who.update(Text(f"{name}", style=f"bold {MUTED}"))
        try:
            pos = service.position(self.account)
        except Exception as exc:
            sub.update(Text(f"Could not read the position: {exc}", style=RED))
            missing.update("")
            notes.update("")
            return
        unknown = service.missing_facts(self.account)
        balance_known = bool(str(profile.get("current_available_balance") or "").strip())
        floor_known = bool(str(profile.get("minimum_balance_to_keep") or "").strip())
        if unknown:
            missing.update(Text(
                "\n".join(
                    f"{line}: fill it in below and press Save facts before deciding."
                    for line in unknown
                ),
                style=AMBER,
            ))
        else:
            missing.update("")
        sub.update(
            Text(
                f"as of {pos['as_of']}  ·  {pos['window_days']}-day window  ·  "
                "conservative, no optional spending changes",
                style=MUTED,
            )
        )
        self.query_one("#m-balance", Metric).update(
            _money(pos["available_balance"]) if balance_known else "—",
            "recorded balance" if balance_known else "not answered yet",
            BLUE if balance_known else AMBER)
        floor = pos["minimum_balance"]
        self.query_one("#m-floor", Metric).update(
            _money(floor) if floor_known else "—",
            "your floor" if floor_known else "not answered yet",
            MUTED if floor_known else AMBER)
        if balance_known and floor_known:
            try:
                headroom = float(pos["headroom"])
            except (TypeError, ValueError):
                headroom = 0.0
            self.query_one("#m-headroom", Metric).update(
                _money(pos["headroom"]), "above the floor",
                GREEN if headroom >= 0 else RED)
        else:
            self.query_one("#m-headroom", Metric).update(
                "—", "needs both facts", MUTED)
        until = pos.get("committed_until")
        self.query_one("#m-committed", Metric).update(
            _money(pos["committed"]),
            f"before income on {until}" if until else "in the projection window",
            AMBER if pos["committed"] not in ("0", "0.0", "") else MUTED)
        if unknown:
            # The kernel would answer this against a zero balance; say so
            # instead of showing a figure nobody's ledger supports.
            self.query_one("#m-safe", Metric).update(
                "—", "not computed: a fact is missing", AMBER)
        else:
            self.query_one("#m-safe", Metric).update(
                _money(pos["max_safe_payment"]),
                "largest one-off today" if not pos.get("fail_closed") else "ledger failed closed",
                GREEN if not pos.get("fail_closed") else RED)
        self.query_one("#m-currency", Metric).update(
            str(pos.get("currency") or "—"), "home currency", MUTED)
        problems = pos.get("problems") or []
        if problems:
            lines = ["Worth knowing:"]
            lines += [f"  · {item}" for item in problems]
        else:
            lines = ["Assumptions:"] + [f"  · {item}" for item in pos["assumptions"]]
        notes.update(Text("\n".join(lines), style=MUTED))

    def refresh_ledger(self) -> None:
        table = self.query_one("#ledger-table", DataTable)
        table.clear()
        if self.account is None:
            return
        events = sorted(self.account.get("events") or [],
                        key=lambda row: str(row.get("event_date") or ""))
        for event in events:
            direction = str(event.get("direction") or "")
            sign = "+" if direction == "credit" else "−" if direction == "debit" else ""
            colour = GREEN if direction == "credit" else RED if direction == "debit" else MUTED
            table.add_row(
                _cell(event.get("event_id"), MUTED),
                _cell(event.get("event_date")),
                _cell(event.get("event_type")),
                _cell(event.get("description")),
                Text(f"{sign}{_money(event.get('amount'))}", style=colour),
                _cell(event.get("status"), MUTED),
            )

    def refresh_decisions(self) -> None:
        table = self.query_one("#decisions-table", DataTable)
        table.clear()
        if self.account is None:
            return
        for row in service.history(self.account):
            request = row.get("request") or {}
            if row.get("error"):
                table.add_row(
                    _cell(request.get("request_id"), MUTED),
                    _cell(request.get("request_date")),
                    _cell(_money(request.get("requested_amount"))),
                    _cell("error", RED), _cell(row["error"], RED), _cell(""),
                )
                continue
            status = str(row.get("affordability_status") or "")
            table.add_row(
                _cell(row.get("request_id"), MUTED),
                _cell(request.get("request_date")),
                _cell(_money(request.get("requested_amount"))),
                _cell(status, STATUS_COLOUR.get(status, TEXT)),
                _cell(row.get("recommended_payment_method")),
                _cell(row.get("payment_plan")),
            )

    def refresh_report(self) -> None:
        banner = self.query_one("#report-banner", Static)
        table = self.query_one("#report-table", DataTable)
        table.clear()
        folder = paths.fixture_dir()
        name = folder.name
        banner.update(Text(
            f"MOCK DATA · calibration fixture '{name}/' · not your money",
            style=f"bold {AMBER}",
        ))
        try:
            import pandas as pd
        except Exception:  # pragma: no cover
            return
        for filename in (
            "requests.csv",
            "financial_profiles.csv",
            "financial_events.csv",
            "request_payment_options.csv",
            "messages.csv",
            "images.csv",
            "exchange_rates.csv",
        ):
            path = folder / filename
            if not path.exists():
                table.add_row(_cell(filename), _cell("missing", RED), _cell(str(path), MUTED))
                continue
            try:
                rows = len(pd.read_csv(path, dtype=str, keep_default_na=False))
            except Exception:
                rows = "unreadable"
            table.add_row(_cell(filename), _cell(rows, BLUE),
                          _cell(str(path.relative_to(paths.repo_root())), MUTED))

    def chat_hello(self) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.clear()
        log.write(Text("Buy or Wait?", style=f"bold {BLUE}"))
        if self.model_choice == model_registry.NO_MODEL:
            log.write(Text(
                "No model is selected, so nothing here can reason about your ledger. "
                "I will not pretend a rule table is a conversation: use the tabs, they "
                "run the kernel directly.",
                style=MUTED,
            ))
            log.write(Text(
                "Pick a local model above to talk to it. Nothing is sent off this "
                "machine when the choice says Local.",
                style=MUTED,
            ))
        else:
            log.write(Text(f"Model: {self.model_choice}", style=GREEN))
            log.write(Text(
                "Ask about your position, a purchase, or a recorded fact. Every figure "
                "in the answer comes from the kernel, through a tool this screen shows.",
                style=MUTED,
            ))

    # ------------------------------------------------------------- the chat

    def _account_refusal(self) -> str | None:
        """Why an action needs a person first, or ``None`` when one is loaded.

        Two stored people and no choice is not an invitation to load the first:
        the answer would be about somebody the person never picked.
        """
        if self.account is not None:
            return None
        if len(service.list_users()) > 1:
            return (
                "No account is selected: choose an account under Switch person "
                "before deciding or asking."
            )
        return "Load a person first."

    def _chat_log(self, message: str, style: str = TEXT) -> None:
        self.query_one("#chat-log", RichLog).write(Text(message, style=style))

    def _set_chat_progress(self, message: str, style: str = MUTED) -> None:
        self.query_one("#chat-progress", Static).update(Text(message, style=style))

    def chat_state(self):
        """This screen's conversation, created on first use and kept in memory.

        One session per loaded person: it carries the previous exchange into the
        next question and remembers which tools the last answer used.  It is
        discarded when another person is loaded and never written to disk.
        """
        from code import chat

        if self.chat_session is None:
            self.chat_session = chat.Session()
        return self.chat_session

    def action_facts(self) -> None:
        """Print the tools the last answer used, or say none has run yet."""
        from code import chat

        facts = chat.facts_used(self.chat_session)
        if not facts:
            self._chat_log(
                "Nothing yet: no answer in this conversation has used a tool.",
                MUTED,
            )
            return
        self._chat_log("Facts used by the last answer:", MUTED)
        for item in facts:
            arguments = ", ".join(
                f"{key}={value}" for key, value in (item.get("args") or {}).items()
            )
            call = f"{item['tool']}({arguments})" if arguments else f"{item['tool']}()"
            self._chat_log(f"  {call} - {item['result_summary']}", MUTED)

    def start_ask(self, text: str, provider) -> Worker | None:
        """Send one question to the model without blocking the screen.

        ``chat.ask`` is synchronous and a provider turn can take seconds, so it
        runs in a thread worker: the screen keeps painting and escape can stop
        waiting for the answer.  Cancelling discards the turn's result; the
        request already in flight cannot be recalled from the provider.
        """
        if self.account is None or provider is None:
            return None
        from code import chat

        session = self.chat_state()
        self._set_chat_progress(
            f"Asking {self.model_choice} — a model can take a while · esc cancels",
            BLUE,
        )
        worker = self.run_worker(
            partial(chat.ask, self.account, text, provider=provider, session=session),
            name="chat-ask",
            group=ASK_GROUP,
            description=f"chat.ask {self.model_choice}",
            exclusive=True,
            thread=True,
            exit_on_error=False,
        )
        self.ask_worker = worker
        return worker

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Render a finished chat turn on the app thread, never in the worker."""
        if self.ask_worker is None or event.worker is not self.ask_worker:
            return
        if event.state == WorkerState.SUCCESS:
            result = event.worker.result or {}
            for name in result.get("tool_calls") or []:
                self._chat_log(f"  ran {name}()", MUTED)
            self._chat_log(str(result.get("reply") or "(no answer)"))
            summary = result.get("run_summary")
            if summary:
                self._chat_log(run_summary_line(summary), MUTED)
            self._set_chat_progress("")
        elif event.state == WorkerState.CANCELLED:
            self._chat_log(
                "Cancelled. A request already sent to the model may still finish; "
                "its answer will not be shown.",
                MUTED,
            )
            self._set_chat_progress("Cancelled.", MUTED)
        elif event.state == WorkerState.ERROR:
            from code import chat

            error = event.worker.error
            if isinstance(error, chat.NoModel):
                self._chat_log(str(error), MUTED)
            else:
                self._chat_log(f"{type(error).__name__}: {error}", RED)
            self._set_chat_progress("The model call failed.", RED)

    def action_cancel_ask(self) -> None:
        """Stop waiting for the running chat turn, if there is one."""
        worker = self.ask_worker
        if worker is None or worker.is_finished:
            return
        worker.cancel()
        if not worker.is_finished:
            # A worker cancelled before its task takes its first step never
            # reaches the handler that reports CANCELLED; record it here so the
            # progress line does not keep claiming the model is still thinking.
            worker.state = WorkerState.CANCELLED

    # -------------------------------------------------------------- actions

    @on(Button.Pressed, "#switch-person")
    def _switch(self, event: Button.Pressed) -> None:
        self.push_screen(UserPicker())

    @on(Button.Pressed, "#chat-facts")
    def _facts_pressed(self, event: Button.Pressed) -> None:
        """The button is the discoverable form of the ``facts`` action."""
        self.action_facts()

    @on(Button.Pressed, "#refresh-position")
    def _refresh(self, event: Button.Pressed) -> None:
        if self.account is not None:
            self.account = service.load_user(
                str((self.account.get("profile") or {}).get("user_id") or "")
            ) or self.account
        self.refresh_all()

    @on(Button.Pressed, "#buy-go")
    def _buy(self, event: Button.Pressed) -> None:
        explain = self.query_one("#buy-explain", Static)
        table = self.query_one("#buy-table", DataTable)
        refusal = self._account_refusal()
        if refusal is not None:
            explain.update(Text(refusal, style=RED))
            table.clear()
            return
        try:
            decision = service.buy(
                self.account,
                self.query_one("#buy-amount", Input).value.strip(),
                on=self.query_one("#buy-on", Input).value.strip() or None,
                by=self.query_one("#buy-by", Input).value.strip() or None,
                text=self.query_one("#buy-text", Input).value.strip(),
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            unknown = service.missing_facts(self.account)
            if unknown:
                # Say where the missing fact can be answered, not just that the
                # decision failed.  The kernel refuses rather than guessing.
                message += (
                    "\nFill in the missing fact on the Position tab, then decide again."
                )
            explain.update(Text(message, style=RED))
            table.clear()
            return
        service.save_user(self.account)
        table.clear()
        status = str(decision.get("affordability_status") or "")
        colour = STATUS_COLOUR.get(status, TEXT)
        rows = [
            ("Verdict", status.replace("_", " "), colour),
            ("Pay now", _money(decision.get("amount_safe_to_pay")), BLUE),
            ("Method", str(decision.get("recommended_payment_method") or ""), TEXT),
            ("Plan", str(decision.get("payment_plan") or "none"), TEXT),
            ("Earliest full payment",
             str(decision.get("earliest_date_for_full_payment") or "—"), TEXT),
            ("Spending changes",
             str(decision.get("spending_changes_needed") or "none"), TEXT),
        ]
        for field, value, style in rows:
            table.add_row(_cell(field, MUTED), _cell(value, style))
        explain.update(Text(decision.get("decision_explanation") or "", style=TEXT))
        self.refresh_position()
        self.refresh_ledger()
        self.refresh_decisions()

    @on(Button.Pressed, "#profile-save")
    def _save_profile(self, event: Button.Pressed) -> None:
        """Record the balance/floor a person fills in; a blank leaves it unknown."""
        status = self.query_one("#profile-status", Static)
        refusal = self._account_refusal()
        if refusal is not None:
            status.update(Text(refusal, style=RED))
            return
        balance = self.query_one("#profile-balance", Input).value.strip()
        floor = self.query_one("#profile-floor", Input).value.strip()
        if not balance and not floor:
            status.update(Text(
                "Enter the balance or the floor you want to record.", style=MUTED))
            return
        try:
            service.update_profile(
                self.account,
                balance=balance or None,
                floor=floor or None,
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            status.update(Text(message, style=RED))
            self.notify(message, severity="error")
            return
        service.save_user(self.account)
        self.query_one("#profile-balance", Input).value = ""
        self.query_one("#profile-floor", Input).value = ""
        still = service.missing_facts(self.account)
        if still:
            status.update(Text("Recorded. Still needed: " + "; ".join(still), style=AMBER))
        else:
            status.update(Text("Recorded.", style=GREEN))
        self.refresh_position()
        self.refresh_decisions()
        self.notify("Recorded", severity="information")

    @on(Button.Pressed, "#fact-add")
    def _add_fact(self, event: Button.Pressed) -> None:
        status = self.query_one("#fact-status", Static)
        refusal = self._account_refusal()
        if refusal is not None:
            status.update(Text(refusal, style=RED))
            self.notify(refusal, severity="error")
            return
        try:
            payload = fact_payload(
                self.query_one("#fact-kind", Input).value,
                self.query_one("#fact-amount", Input).value,
                when=self.query_one("#fact-date", Input).value,
                description=self.query_one("#fact-text", Input).value,
                category=self.query_one("#fact-category", Input).value,
                recurrence=self.query_one("#fact-recurrence", Input).value,
            )
            event = service.add_fact(self.account, **payload)
        except Exception as exc:
            # The refusal belongs next to the fields, not only in a toast.
            message = f"{type(exc).__name__}: {exc}"
            status.update(Text(message, style=RED))
            self.notify(message, severity="error")
            return
        service.save_user(self.account)
        self.query_one("#fact-amount", Input).value = ""
        self.query_one("#fact-text", Input).value = ""
        recorded = str(event.get("category") or payload["kind"])
        status.update(Text(f"Recorded {recorded}.", style=GREEN))
        self.refresh_position()
        self.refresh_ledger()
        self.notify("Recorded", severity="information")

    @on(Select.Changed, "#model-select")
    def _model_changed(self, event: Select.Changed) -> None:
        self.model_choice = str(event.value or model_registry.NO_MODEL)
        log = self.query_one("#chat-log", RichLog)
        if self.model_choice == model_registry.NO_MODEL:
            log.write(Text(
                "No model selected. The tabs still run the kernel directly.", style=MUTED))
        else:
            log.write(Text(f"Model: {self.model_choice}", style=GREEN))

    @on(Input.Submitted, "#chat-input")
    def _chat(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        refusal = self._account_refusal()
        if refusal is not None:
            self._chat_log(refusal, RED)
            self._set_chat_progress(refusal, RED)
            return
        self._chat_log(f"you: {text}", BLUE)
        if self.model_choice == model_registry.NO_MODEL:
            message = (
                "No model is selected, so I will not guess. The tabs run the kernel "
                "directly: Position for what is safe today, Purchase for a decision."
            )
            self._chat_log(message, MUTED)
            return
        try:
            provider = model_registry.provider_for(self.model_choice)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self._chat_log(message, RED)
            self._set_chat_progress(message, RED)
            return
        if provider is None:
            message = (
                f"The selected model ({self.model_choice}) is not configured, so "
                "there is nothing to ask. The tabs run the kernel directly."
            )
            self._chat_log(message, MUTED)
            self._set_chat_progress(message, MUTED)
            return
        self.start_ask(text, provider)


def main(argv=None) -> int:
    BuyOrWait().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
