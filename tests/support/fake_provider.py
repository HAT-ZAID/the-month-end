"""A scripted stand-in for the tool-calling chat protocol.

A chat loop only asks a provider for three things: an identity (``name``,
``model``, ``privacy``), a call budget (``reserve_call`` and ``remaining_calls``),
and a chat model exposing ``bind_tools`` and ``invoke``.  :class:`FakeProvider`
implements exactly that, replaying scripted tool batches and replies, so a
protocol test needs no network, no SDK and no live model.

Deliberately imports nothing from :mod:`code` and nothing from ``langchain_core``:
the double has to stay importable where the optional model stack is not
installed, and a test written against it must fail for a reason in the code under
test, never because the double drifted with an SDK.

Usage::

    provider = FakeProvider(
        tool_batches=[[{"name": "position", "args": {}, "id": "call_1"}]],
        replies=["You can spend 10."],
    )
    bound = provider.chat_model().bind_tools(TOOL_SCHEMAS)
    first = bound.invoke(messages)   # one tool call, content ""
    second = bound.invoke(messages)  # the reply, no tool calls

Every ``invoke`` is one model turn: it increments ``calls``, waits ``delay``
seconds, then replays the next tool batch, otherwise the next reply. A script that
runs out answers with :data:`EXHAUSTED_REPLY` and no tool calls, so a loop always
terminates instead of spinning on an empty script.
"""
from __future__ import annotations

import time

DEFAULT_MAX_CALLS = 50
EXHAUSTED_REPLY = "no further scripted turns"


def _sleep_at_least(seconds: float) -> None:
    """Hold for ``seconds`` or longer; ``time.sleep`` alone returns early on Windows.

    A double with an under-sleeping delay would make every timeout test flaky, so
    the delay is enforced against the monotonic clock rather than trusted.
    """
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(remaining)


def _normalise_calls(tool_calls) -> list[dict]:
    """Each call is a plain dict with ``name``, ``args`` and ``id``; blanks default."""
    cleaned: list[dict] = []
    for index, call in enumerate(tool_calls or ()):
        entry = dict(call or {})
        cleaned.append(
            {
                "name": str(entry.get("name") or ""),
                "args": dict(entry.get("args") or {}),
                "id": str(entry.get("id") or f"call_{index + 1}"),
            }
        )
    return cleaned


class FakeMessage:
    """The two fields a chat loop reads from an assistant turn."""

    def __init__(self, content: str = "", tool_calls=None):
        self.content = str(content or "")
        self.tool_calls = _normalise_calls(tool_calls)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"FakeMessage(content={self.content!r}, tool_calls={self.tool_calls!r})"


class FakeChatModel:
    """A chat model whose turns come from the script on its provider."""

    def __init__(self, provider: "FakeProvider"):
        self.provider = provider

    def bind_tools(self, schemas, **kwargs):
        """Accept the schemas; raise only when the script asks for that failure."""
        if self.provider.bind_raises:
            raise RuntimeError("scripted failure: this model cannot bind tools")
        return self

    def invoke(self, messages, **kwargs):
        """Return the next scripted turn; the prompt is never inspected."""
        return self.provider._next_turn()


class FakeProvider:
    """Provider-shaped replay of ``tool_batches`` then ``replies``."""

    def __init__(
        self,
        replies=None,
        tool_batches=None,
        bind_raises=False,
        delay=0.0,
        name="fake",
        privacy="local",
        model="fake-1",
        max_calls=50,
    ):
        self.replies = list(replies or ())
        self.tool_batches = [list(batch or ()) for batch in (tool_batches or ())]
        self.bind_raises = bool(bind_raises)
        self.delay = max(0.0, float(delay or 0.0))
        self.name = str(name)
        self.privacy = str(privacy)
        self.model = str(model)
        # Mirrors code.providers.ChatProvider: the run cap is never zero.
        self.max_calls = max(1, int(max_calls))
        #: Model turns actually served, so a test can count the loop.
        self.calls = 0
        #: Turns claimed from the run budget (a reserved turn need not be served).
        self.reserved_calls = 0
        self._model = FakeChatModel(self)

    # -- the provider contract ------------------------------------------------

    def chat_model(self):
        """The one unbound model; repeated calls return the same instance."""
        return self._model

    def reserve_call(self) -> bool:
        """Claim one turn from ``max_calls``; ``False`` once the budget is spent.

        Counted separately from :attr:`calls`, exactly like
        :class:`code.providers.ChatProvider`, so a refused or failed turn still
        consumes the run budget.
        """
        if self.reserved_calls >= self.max_calls:
            return False
        self.reserved_calls += 1
        return True

    @property
    def remaining_calls(self) -> int:
        return max(0, self.max_calls - self.reserved_calls)

    # -- replay ---------------------------------------------------------------

    def _next_turn(self) -> FakeMessage:
        self.calls += 1
        if self.delay:
            _sleep_at_least(self.delay)
        if self.tool_batches:
            return FakeMessage("", self.tool_batches.pop(0))
        if self.replies:
            reply = self.replies.pop(0)
            return reply if isinstance(reply, FakeMessage) else FakeMessage(reply)
        return FakeMessage(EXHAUSTED_REPLY)


__all__ = [
    "DEFAULT_MAX_CALLS",
    "EXHAUSTED_REPLY",
    "FakeChatModel",
    "FakeMessage",
    "FakeProvider",
]
