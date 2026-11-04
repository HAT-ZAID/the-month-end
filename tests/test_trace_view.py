"""Task 6B: static trace viewer + TUI `trace` command tests.

Everything here is synthetic: no dataset load, no solver, no provider, no
browser. The trace fixtures mirror what ``code.trace.TraceRecorder.finish``
writes, so the viewer is tested against the real contract.
"""
import io
import json

from rich.console import Console

from tools import trace_view, tui


FINAL_ROW = {
    "request_id": "request_001",
    "amount_safe_to_pay": "60.00",
    "affordability_status": "affordable_with_plan",
    "recommended_payment_method": "partial_payment",
    "payment_plan": "2026-01-05:60.00|2026-01-20:40.00",
    "earliest_date_for_full_payment": "2026-01-20",
    "spending_changes_needed": "none",
    "decision_explanation": "Two payments keep the floor intact.",
}

#: The eight output.csv columns, in contract order.
OUTPUT_COLUMNS = (
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
)

CANDIDATE = {
    "request_id": "request_001",
    "amount_safe_to_pay": "60.00",
    "affordability_status": "affordable_with_plan",
    "recommended_payment_method": "partial_payment",
    "payment_plan": "2026-01-05:60.00|2026-01-20:40.00",
    "earliest_date_for_full_payment": "2026-01-20",
    "spending_changes_needed": "none",
    "evidence_sufficient": True,
    "confidence": 0.8,
    "rationale": "Two payments keep the floor intact.",
}


def make_trace(request_id="request_001", **overrides):
    trace = {
        "schema_version": 2,
        "run_id": "run-test",
        "request_id": request_id,
        "provider": "fake",
        "model": "fake-model",
        "prompt_version": "agent-v1",
        "prompt_hash": "abc123def456",
        "events": [{"kind": "request_start", "request_id": request_id}],
        "model_turns": [
            {"role": "assistant", "text": "looking", "tool_calls": [], "usage": {"total_tokens": 150}}
        ],
        "tool_calls": [
            {
                "name": "search_evidence",
                "args": {"request_id": request_id, "query": "salary"},
                "ok": True,
                "error": None,
                "duration_ms": 1.5,
            },
            {
                "name": "get_cashflow_projection",
                "args": {"request_id": request_id},
                "ok": False,
                "error": "no ledger",
                "duration_ms": 2.25,
            },
        ],
        "retrieval": {"backend": "sqlite", "warnings": [], "queries": 1},
        "candidate": dict(CANDIDATE, request_id=request_id),
        "verification": {
            "agent_agreement": True,
            "differences": [],
            "candidate_valid": True,
            "fallback_reason": None,
            "rationale_source": "candidate",
        },
        "final_row": dict(FINAL_ROW, request_id=request_id),
        "usage": {
            "provider": "fake",
            "model": "fake-model",
            "model_calls": 2,
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "api_key": "sk-live-ABCDEFGH12345678",
            "access_token": "tok_abcdef123456",
        },
        "budget": {
            "provider_calls_used": 2,
            "provider_calls_limit": 6,
            "tool_calls_used": 2,
            "tool_calls_limit": 6,
        },
        "fallback_reason": None,
        "metadata": {"created_at": "2026-10-29T00:00:00Z", "duration_ms": 12.0},
    }
    trace.update(overrides)
    return trace


def write_trace(directory, trace):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{trace['request_id']}.json"
    path.write_text(json.dumps(trace), encoding="utf-8")
    return path


def make_console():
    buffer = io.StringIO()
    console = Console(
        file=buffer, width=110, force_terminal=False, highlight=False, markup=False
    )
    return console, buffer


# ---------------------------------------------------------------------------
# Detail page
# ---------------------------------------------------------------------------


def test_detail_page_shows_request_status_and_tool_names(tmp_path):
    path = write_trace(tmp_path / "run-test", make_trace())
    out = tmp_path / "request_001.html"

    code = trace_view.main(["--trace", str(path), "--out", str(out)])

    assert code == 0
    html = out.read_text(encoding="utf-8")
    assert "request_001" in html
    assert "affordable_with_plan" in html and "partial_payment" in html
    assert "search_evidence" in html
    assert "get_cashflow_projection" in html
    assert "fake-model" in html
    assert "2026-01-05:60.00|2026-01-20:40.00" in html
    assert html.lstrip().startswith("<!DOCTYPE html>")

    # The final row is shown as the exact eight-column output contract.
    contract = html[html.index("Final enforced row") :]
    contract = contract[: contract.index("</table>")]
    positions = [contract.index(f"<th>{column}</th>") for column in OUTPUT_COLUMNS]
    assert positions == sorted(positions)
    assert "Two payments keep the floor intact." in contract


def test_trace_text_is_escaped_never_executable(tmp_path):
    hostile = '<script>alert("x")</script>'
    trace = make_trace()
    trace["tool_calls"][0]["args"] = {"request_id": "request_001", "query": hostile}
    trace["final_row"]["decision_explanation"] = hostile
    path = write_trace(tmp_path / "run-test", trace)

    html = trace_view.render_detail(trace_view.load_trace(path))

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert hostile not in html


def test_secrets_are_redacted_but_token_counters_survive(tmp_path):
    path = write_trace(tmp_path / "run-test", make_trace())

    html = trace_view.render_detail(trace_view.load_trace(path))

    assert "sk-live-ABCDEFGH12345678" not in html
    assert "tok_abcdef123456" not in html
    assert trace_view.REDACTED in html
    assert "total_tokens" in html
    assert "150" in html


def test_trace_without_out_prints_html_to_stdout(tmp_path, capsys):
    path = write_trace(tmp_path / "run-test", make_trace())

    code = trace_view.main(["--trace", str(path)])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.lstrip().startswith("<!DOCTYPE html>")
    assert "request_001" in captured.out
    assert captured.err == ""


def test_missing_trace_exits_nonzero_with_readable_message(tmp_path, capsys):
    code = trace_view.main(["--trace", str(tmp_path / "nope.json")])

    captured = capsys.readouterr()
    assert code != 0
    assert "nope.json" in captured.err
    assert "Traceback" not in captured.err


def test_malformed_trace_exits_nonzero_with_readable_message(tmp_path, capsys):
    broken = tmp_path / "request_001.json"
    broken.write_text("{not json", encoding="utf-8")

    code = trace_view.main(["--trace", str(broken)])

    captured = capsys.readouterr()
    assert code != 0
    assert "not valid JSON" in captured.err
    assert "Traceback" not in captured.err


# ---------------------------------------------------------------------------
# Index page
# ---------------------------------------------------------------------------


def test_trace_dir_writes_index_and_one_page_per_trace(tmp_path):
    root = tmp_path / "traces" / "run-test"
    write_trace(root, make_trace("request_001"))
    write_trace(root, make_trace("request_002"))
    out = root / "index.html"

    code = trace_view.main(["--trace-dir", str(root), "--out", str(out)])

    assert code == 0
    index = out.read_text(encoding="utf-8")
    assert "run-test" in index
    assert "2" in index  # trace count
    assert 'href="request_001.html"' in index
    assert 'href="request_002.html"' in index
    assert "fake-model" in index
    assert "affordable_with_plan" in index
    assert (root / "request_001.html").exists()
    assert (root / "request_002.html").exists()


def test_trace_dir_without_out_is_a_clear_error(tmp_path, capsys):
    root = tmp_path / "run-test"
    write_trace(root, make_trace())

    code = trace_view.main(["--trace-dir", str(root)])

    captured = capsys.readouterr()
    assert code != 0
    assert "--out" in captured.err
    assert "Traceback" not in captured.err


def test_empty_trace_dir_is_a_clear_error(tmp_path, capsys):
    (tmp_path / "empty").mkdir()

    code = trace_view.main(["--trace-dir", str(tmp_path / "empty"), "--out", str(tmp_path / "i.html")])

    captured = capsys.readouterr()
    assert code != 0
    assert "no" in captured.err.lower()
    assert "Traceback" not in captured.err


# ---------------------------------------------------------------------------
# TUI command
# ---------------------------------------------------------------------------


def test_tui_trace_summarizes_the_trace(tmp_path):
    root = tmp_path / "traces"
    path = write_trace(root / "run-test", make_trace())
    console, buffer = make_console()
    app = tui.TUI(data={}, console=console, trace_dir=root)

    app.cmd_trace(["request_001"])

    output = buffer.getvalue()
    assert "request_001" in output
    assert "fake-model" in output
    assert "search_evidence" in output
    assert "get_cashflow_projection" in output
    assert str(path) in output or "request_001.json" in output


def test_tui_trace_reports_a_missing_trace(tmp_path):
    root = tmp_path / "traces"
    write_trace(root / "run-test", make_trace())
    console, buffer = make_console()
    app = tui.TUI(data={}, console=console, trace_dir=root)

    app.cmd_trace(["request_404"])

    output = buffer.getvalue()
    assert "request_404" in output
    assert "no trace" in output.lower()


def test_tui_help_lists_the_trace_command():
    assert "trace <id>" in tui.HELP_TEXT


def test_redaction_covers_hyphenated_and_prefixed_secret_names():
    from code.trace import sanitize

    payload = {
        "x-api-key": "SECRETVALUE",
        "Api-Key": "SECRETVALUE",
        "refresh-token": "SECRETVALUE",
        "authorization": "SECRETVALUE",
        "note": "plain",
    }
    cleaned = sanitize(payload)
    assert "SECRETVALUE" not in repr(cleaned)
    assert cleaned["note"] == "plain"


def test_redaction_covers_camel_case_secret_names():
    from code.trace import REDACTED, sanitize

    keys = {
        "sessionToken": "K6",
        "SessionToken": "K6",
        "clientSecret": "K7",
        "ClientSecret": "K7",
        "apiKey": "K8",
        "APIKey": "K8",
        "privateKey": "K9",
        "accessToken": "K10",
    }
    cleaned = sanitize(dict(keys))
    for name in keys:
        assert cleaned[name] == REDACTED, name
    assert sanitize({"total_tokens": 12}) == {"total_tokens": 12}


def test_redaction_covers_capitalised_and_cookie_secret_names():
    from code.trace import REDACTED, sanitize

    payload = {
        "PrivateKey": "P1",
        "SessionToken": "S2",
        "ClientSecret": "C3",
        "IdToken": "I4",
        "SetCookie": "K5",
        "total_tokens": 12,
    }
    cleaned = sanitize(payload)
    for name in ("PrivateKey", "SessionToken", "ClientSecret", "IdToken"):
        assert cleaned[name] == REDACTED, name
    assert cleaned["total_tokens"] == 12


def test_redaction_covers_cloud_and_oauth_token_prefixes():
    from code.trace import REDACTED, sanitize

    secrets = [
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_0123456789abcdefghijABCDEFGHIJ012345",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    ]
    for value in secrets:
        cleaned = sanitize({"detail": f"failed with {value} attached"})
        assert value not in cleaned["detail"]
        assert REDACTED in cleaned["detail"]
    basic = sanitize({"detail": "Authorization: Basic dXNlcjpwYXNzd29yZA=="})
    assert "dXNlcjpwYXNzd29yZA" not in basic["detail"]


def test_redaction_covers_cookie_fields():
    from code.trace import sanitize

    cleaned = sanitize(
        {
            "Cookie": "session=abc123",
            "Set-Cookie": "session=def456",
            "setCookie": "ghi789",
            "SetCookie": "jkl012",
            "CookieJar": "mno345",
            "keep": "pqr678",
        }
    )
    for name in ("Cookie", "Set-Cookie", "setCookie", "SetCookie"):
        assert cleaned[name] == "[REDACTED]"
    assert cleaned["CookieJar"] == "mno345"
    assert cleaned["keep"] == "pqr678"


def test_redaction_covers_the_shapes_a_provider_error_carries():
    from code.trace import sanitize

    cleaned = sanitize(
        {
            "pem": ("-" * 5) + "BEGIN RSA PRIVATE KEY" + ("-" * 5) + "\nMIIE\n" + ("-" * 5) + "END RSA PRIVATE KEY" + ("-" * 5),
            "dsn": "password=hunter2xyz",
            "google": "AIza" + "S" * 33,
            "azure": "AccountKey=abcdefghijkl==",
            "keep": "account balance 500000",
        }
    )
    assert cleaned["pem"] == "[REDACTED]"
    assert "hunter2" not in cleaned["dsn"]
    assert cleaned["google"] == "[REDACTED]"
    assert "abcdefghijkl" not in cleaned["azure"]
    assert cleaned["keep"] == "account balance 500000"


def test_redaction_covers_the_shapes_a_gateway_error_echoes():
    from code.trace import sanitize

    for text in (
        "401 Received API Key = gwk_live_7f3a9c2e rejected",
        "invalid API key for abcdef123456",
        "sigv4 Sig=deadbeefdeadbeefdeadbeef",
        "url?sv=2022&sig=abcdef123456",
        "Cookie: sid=abcdef123456",
        "Set-Cookie: sid=abcdef123456",
    ):
        assert "[REDACTED]" in sanitize({"e": text})["e"], text


def test_a_multi_colon_scheme_less_userinfo_is_redacted_whole():
    from code.trace import REDACTED, sanitize

    cleaned = sanitize(
        {"e": "connect failed for alice:part1:part2:part3@gateway.example"}
    )["e"]
    assert cleaned == f"connect failed for alice:{REDACTED}@gateway.example"
    for fragment in ("part1", "part2", "part3"):
        assert fragment not in cleaned


def test_a_registered_secret_is_redacted_whatever_its_shape():
    from code.trace import register_secret, sanitize

    register_secret("plainkeyvalue12345")
    assert sanitize({"e": "boom plainkeyvalue12345 today"})["e"] == "boom [REDACTED] today"


def test_a_set_value_is_serialisable():
    from code.trace import sanitize

    assert sanitize({"e": {"b", "a"}}) == {"e": ["a", "b"]}


def test_redaction_covers_the_round_nine_shapes():
    from code.trace import register_secret, sanitize

    assert "gwpass" not in sanitize(
        {"e": "connect failed for gwuser:gwpass@gateway.example.test:443"}
    )["e"]
    assert "abcdef" not in sanitize({"e": "sent cookie=session%3Dabcdef upstream"})["e"]
    assert "horse battery" not in sanitize(
        {"e": "the password is correct horse battery staple"}
    )["e"]
    assert "horse battery" not in sanitize(
        {"e": 'password="correct horse battery staple"'}
    )["e"]

    register_secret("keyaskey-123456")
    cleaned = sanitize({"keyaskey-123456": 1, "e": "x keyaskey-123456 y"})
    assert "[REDACTED]" in cleaned
    assert cleaned["e"] == "x [REDACTED] y"


def test_an_ssh2_armoured_key_is_redacted():
    from code.trace import sanitize

    header = ("-" * 4) + " BEGIN SSH2 ENCRYPTED PRIVATE KEY " + ("-" * 4)
    footer = ("-" * 4) + " END SSH2 ENCRYPTED PRIVATE KEY " + ("-" * 4)
    cleaned = sanitize({"e": header + "\nMIIE\n" + footer})
    assert "MIIE" not in cleaned["e"]


def test_a_huge_hostile_body_does_not_stall_the_scrubber():
    import time

    from code.trace import sanitize

    header = ("-" * 5) + " BEGIN RSA PRIVATE KEY " + ("-" * 5)
    body = header * 2000
    started = time.monotonic()
    sanitize({"e": body})
    assert time.monotonic() - started < 2.0
