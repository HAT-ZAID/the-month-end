"""CR-11: an attempt, a healthy backend and actual evidence are three facts.

The old run state set one ``evidence`` flag whenever ``search_evidence`` returned
a successful envelope, so a zero-citation search was indistinguishable from a
search that really retrieved a message.  These tests pin the three flags apart
and pin the required-evidence gate to ``evidence_found``, not to the attempt.
"""
from __future__ import annotations

import json
from pathlib import Path

from code.agent import _missing_steps, _note_step
from code.trace import TraceRecorder

REQUEST_ID = "request_demo"

_ONE_CITATION = {
    "chunk_id": "message:message_01",
    "source_type": "message",
    "source_id": "message_01",
    "text": "Employer payroll note: the January salary lands on 2026-01-10.",
}


def _state(**overrides) -> dict:
    state = {
        "search_attempted": False,
        "search_backend_ok": False,
        "evidence_found": False,
        "projection": False,
    }
    state.update(overrides)
    return state


def _envelope(citations) -> dict:
    return {
        "ok": True,
        "data": {
            "backend": "lexical",
            "count": len(citations),
            "citations": list(citations),
        },
    }


def test_zero_citations_is_attempted_and_healthy_but_not_evidence():
    """A healthy search that found nothing must not claim it found evidence."""
    state = _state()

    _note_step(None, state, "search_evidence", _envelope([]), None, False)

    assert state["search_attempted"] is True
    assert state["search_backend_ok"] is True
    assert state["evidence_found"] is False


def test_one_citation_sets_all_three_flags():
    state = _state()

    _note_step(
        None, state, "search_evidence", _envelope([_ONE_CITATION]), None, False
    )

    assert state["search_attempted"] is True
    assert state["search_backend_ok"] is True
    assert state["evidence_found"] is True
    assert state["retrieval"] == "lexical"


def test_failed_search_is_attempted_but_the_backend_is_not_ok():
    state = _state()

    _note_step(
        None,
        state,
        "search_evidence",
        {"ok": False, "error": "retrieval backend unavailable"},
        None,
        False,
    )

    assert state["search_attempted"] is True
    assert state["search_backend_ok"] is False
    assert state["evidence_found"] is False


def test_required_evidence_gate_reads_evidence_found_not_the_attempt():
    """A zero-citation search does not satisfy the required evidence step."""
    state = _state(search_attempted=True, search_backend_ok=True, projection=True)

    assert _missing_steps(state) == ["search_evidence"]

    state["evidence_found"] = True
    assert _missing_steps(state) == []


def test_trace_retrieval_result_reports_the_three_flags_consistently(tmp_path):
    recorder = TraceRecorder("run-evidence", tmp_path / "traces")
    state = _state()

    _note_step(None, state, "search_evidence", _envelope([]), recorder, True)
    path = recorder.finish(
        request_id=REQUEST_ID,
        retrieval={
            "backend": state["retrieval"],
            "search_attempted": state["search_attempted"],
            "search_backend_ok": state["search_backend_ok"],
            "evidence_found": state["evidence_found"],
        },
    )
    payload = json.loads(Path(path).read_text(encoding="utf-8"))

    events = [
        event for event in payload["events"] if event["kind"] == "retrieval_result"
    ]
    assert len(events) == 1
    assert events[0]["count"] == 0
    assert events[0]["citations"] == []
    assert events[0]["search_attempted"] is True
    assert events[0]["search_backend_ok"] is True
    assert events[0]["evidence_found"] is False
