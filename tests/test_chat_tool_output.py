"""A tool result sent back to the model is always valid JSON (issue #16).

The old ``_tool_message`` serialised a result and cut the text at 4000
characters.  A cut can land inside a string, between a key and its value, or in
the middle of a list, so the next model turn received something ``json.loads``
cannot read.  The replacement never slices serialized JSON: a result that fits
is unchanged, and a larger one is rebuilt as a whole-value envelope.
"""
from __future__ import annotations

import json

from code import chat


def _content(result) -> str:
    return chat._tool_message("call_1", result)["content"]


def _payload(result) -> dict:
    return json.loads(_content(result))


def test_a_small_result_is_serialized_unchanged():
    result = {"events": [{"event_id": "event_1", "amount": "1.00"}]}
    message = chat._tool_message("call_1", result)

    assert message["role"] == "tool"
    assert message["tool_call_id"] == "call_1"
    assert message["content"] == json.dumps(result, default=str)
    assert json.loads(message["content"]) == result
    assert len(message["content"]) <= chat.MAX_TOOL_RESULT_CHARS


def test_a_500_item_result_is_a_valid_truncated_envelope():
    result = {
        "events": [
            {
                "event_id": f"event_{index:03d}",
                "amount": "1.00",
                "description": "x" * 40,
            }
            for index in range(500)
        ]
    }

    text = _content(result)
    payload = json.loads(text)

    assert len(text) <= chat.MAX_TOOL_RESULT_CHARS
    assert payload["truncated"] is True
    assert payload["total_count"] == 500
    assert payload["returned_count"] == len(payload["items"])
    assert len(payload["items"]) < payload["total_count"]
    assert payload["has_more"] is True
    assert payload["field"] == "events"
    # Every returned item is whole: no value was split by a character cut.
    assert all(isinstance(item, dict) for item in payload["items"])
    assert all(item["event_id"].startswith("event_") for item in payload["items"])


def test_a_bare_list_is_wrapped_not_sliced():
    result = [{"id": index, "text": "y" * 50} for index in range(500)]

    payload = _payload(result)

    assert payload["truncated"] is True
    assert payload["total_count"] == len(result)
    assert payload["returned_count"] == len(payload["items"])
    assert len(payload["items"]) < payload["total_count"]
    assert payload["has_more"] is True


def test_an_oversized_non_list_payload_becomes_a_preview():
    result = {"decision_explanation": "z" * 10_000}

    text = _content(result)
    payload = json.loads(text)

    assert len(text) <= chat.MAX_TOOL_RESULT_CHARS
    assert payload["truncated"] is True
    assert payload["reason"] == "oversized"
    assert isinstance(payload["preview"], str)
    assert payload["preview"]
    assert set(payload) == {"truncated", "preview", "reason"}


def test_a_list_payload_whose_first_item_does_not_fit_returns_no_items():
    result = {"rows": [{"text": "q" * 5000}]}

    payload = _payload(result)

    assert payload["truncated"] is True
    assert payload["total_count"] == 1
    assert payload["returned_count"] == 0
    assert payload["items"] == []
    assert payload["has_more"] is True
