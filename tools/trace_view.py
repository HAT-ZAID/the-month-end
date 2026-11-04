"""Read-only, standalone HTML viewer for the per-request agent traces (Task 6B).

The agent layer already writes one JSON trace per request (``code.trace``)::

    traces/<run_id>/<request_id>.json

This module turns those files into local HTML pages.  It is deliberately
static: inline CSS, no server, no CDN, no fonts, no JavaScript.  Every value is
HTML-escaped, because trace text (tool arguments, rationale, provider errors)
is untrusted evidence, and every value is re-run through
:func:`code.trace.sanitize` before rendering, so the viewer can never publish a
secret the recorder would have redacted.

Two entry points, both read-only::

    python -m tools.trace_view --trace-dir traces/run --out traces/run/index.html
    python -m tools.trace_view --trace traces/run/request_demo.json --out request_demo.html

``output.csv`` is never read or written.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from code.schema import FINAL_FIELDS
from code.trace import REDACTED, _safe_name, sanitize

#: The exact eight-column ``output.csv`` contract.  ``FINAL_FIELDS`` covers the
#: seven machine fields; ``decision_explanation`` is the human column the
#: verifier writes last, so the viewer shows all eight in contract order.
OUTPUT_FIELDS = FINAL_FIELDS + ("decision_explanation",)

#: Where ``python -m tools.tui`` looks for traces unless told otherwise.
DEFAULT_TRACE_DIR = "traces/latest"

#: Arguments and result text are clipped so one hostile value cannot dominate.
MAX_SUMMARY_CHARS = 220
MAX_RAW_CHARS = 400_000
MAX_INDEX_ROWS = 500

STATUS_CLASSES = {
    "affordable_now": "ok",
    "affordable_with_plan": "plan",
    "affordable_later": "later",
    "not_affordable": "bad",
}

_INDEX_CSS = """\
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 28px; max-width: 1180px;
       font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
       font-size: 14px; line-height: 1.5; background: #fafafc; color: #1b1b1f; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .08em;
     margin: 30px 0 8px; color: #52525b; }
p.sub { margin: 0; color: #52525b; }
a { color: #1d4ed8; }
table { border-collapse: collapse; width: 100%; background: #fff; }
th, td { border: 1px solid #d8d8e0; padding: 6px 9px; text-align: left;
         vertical-align: top; }
th { background: #eeeef4; font-weight: 600; white-space: nowrap; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
pre { margin: 0; padding: 12px; background: #f4f4f8; border: 1px solid #d8d8e0;
      overflow-x: auto; white-space: pre-wrap; word-break: break-word; }
code { background: #f4f4f8; padding: 1px 4px; }
details { margin-top: 10px; }
summary { cursor: pointer; color: #52525b; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 999px;
         border: 1px solid currentColor; font-size: 12px; font-weight: 600; }
.badge.ok { color: #15803d; }
.badge.plan { color: #a16207; }
.badge.later { color: #0e7490; }
.badge.bad { color: #b91c1c; }
.badge.unknown { color: #52525b; }
.note { color: #52525b; }
.warn { color: #b91c1c; }
footer { margin-top: 36px; padding-top: 12px; border-top: 1px solid #d8d8e0;
         color: #52525b; font-size: 12px; }
@media (prefers-color-scheme: dark) {
  body { background: #14141a; color: #e6e6ee; }
  table, pre, code { background: #1c1c24; }
  th { background: #24242e; }
  th, td, pre, footer { border-color: #33333f; }
  a { color: #93b4ff; }
}
"""


class TraceError(Exception):
    """A trace file or trace directory that cannot be rendered."""


class TraceEntry(NamedTuple):
    """One loaded trace plus the file it came from."""

    path: Path
    trace: dict


# ---------------------------------------------------------------------------
# Small value helpers
# ---------------------------------------------------------------------------


def _text(value) -> str:
    if value is None:
        return ""
    text = str(value)
    return "" if text.strip().lower() in {"nan", "nat"} else text


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _esc(value, limit: int | None = None) -> str:
    """Escape a trace value for HTML text *and* attribute context."""
    text = _text(value)
    if limit is not None and len(text) > limit:
        text = text[: max(limit - 1, 0)] + "\u2026"
    return html.escape(text, quote=True)


def _clip(value, limit: int = MAX_SUMMARY_CHARS) -> str:
    text = _text(value)
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)] + "\u2026"


def _json_summary(value) -> str:
    """Compact one-line JSON for tool arguments; hostile text stays text."""
    if value in (None, {}, []):
        return ""
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(value)
    return _clip(text, MAX_SUMMARY_CHARS)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_trace(path) -> dict:
    """Read, validate and redact one trace JSON file.

    Raises :class:`TraceError` (never a traceback) for a missing file, invalid
    JSON, a non-object payload or a payload without a ``request_id``.
    """
    source = Path(path)
    if not source.exists():
        raise TraceError(f"no trace file at {source}")
    if not source.is_file():
        raise TraceError(f"{source} is not a file; pass one trace JSON file")
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise TraceError(f"could not read {source}: {exc}") from None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TraceError(
            f"{source} is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})"
        ) from None
    if not isinstance(payload, dict):
        raise TraceError(f"{source} is not a JSON object; expected one request trace")
    if not _text(payload.get("request_id")).strip():
        raise TraceError(
            f"{source} has no request_id; it is not a request trace "
            "(the recorder writes <run_id>/<request_id>.json)"
        )
    # Second pass over an untrusted file: the recorder already redacts, but a
    # hand-edited or older trace must not be able to publish a secret either.
    return sanitize(payload)


def find_traces(trace_dir) -> list[Path]:
    """Every ``*.json`` trace under ``trace_dir``, sorted by path."""
    root = Path(trace_dir)
    if not root.exists():
        raise TraceError(f"no trace directory at {root}")
    if not root.is_dir():
        raise TraceError(f"{root} is not a directory; pass a run directory of traces")
    files = sorted(path for path in root.rglob("*.json") if path.is_file())
    if not files:
        raise TraceError(f"no *.json traces under {root}")
    return files


def find_trace(trace_dir, request_id) -> Path | None:
    """Locate one request's trace file under ``trace_dir`` (newest wins)."""
    root = Path(trace_dir)
    wanted = _text(request_id).strip()
    if not wanted:
        return None
    stem = f"{_safe_name(wanted)}.json"
    direct = root / stem
    if direct.is_file():
        return direct
    if not root.is_dir():
        return None
    found = [path for path in root.rglob(stem) if path.is_file()]
    if not found:
        # A trace may sit under a differently named file; match its own id.
        for path in sorted(root.rglob("*.json")):
            if path.name == "index.json":
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(payload, dict) and _text(payload.get("request_id")).strip() == wanted:
                found.append(path)
    if not found:
        return None
    return max(found, key=lambda path: path.stat().st_mtime)


# ---------------------------------------------------------------------------
# Summaries shared by the HTML pages and the TUI
# ---------------------------------------------------------------------------


def final_row(trace) -> dict:
    """The final enforced row, restricted to the exact eight-column contract."""
    source = _dict(_dict(trace).get("final_row"))
    return {field: _text(source.get(field)) for field in OUTPUT_FIELDS}


def tool_calls(trace) -> list[dict]:
    return [call for call in _list(_dict(trace).get("tool_calls")) if isinstance(call, dict)]


def model_calls(trace) -> int:
    payload = _dict(trace)
    usage = _dict(payload.get("usage"))
    budget = _dict(payload.get("budget"))
    metadata = _dict(payload.get("metadata"))
    for value in (
        _int(usage.get("model_calls")),
        _int(budget.get("provider_calls_used")),
        _int(metadata.get("model_turns")),
    ):
        if value is not None:
            return value
    return len(_list(payload.get("model_turns")))


def token_total(trace) -> int | None:
    usage = _dict(_dict(trace).get("usage"))
    total = _int(usage.get("total_tokens"))
    if total is not None:
        return total
    parts = [_int(usage.get("input_tokens")), _int(usage.get("output_tokens"))]
    known = [part for part in parts if part is not None]
    return sum(known) if known else None


def candidate_differences(trace) -> list[dict]:
    """Where the proposal differed from the enforced row.

    The verifier's own record is authoritative; when it is absent (an older or
    hand-written trace) the candidate is compared with the final row directly.
    """
    verification = _dict(_dict(trace).get("verification"))
    recorded = []
    for item in _list(verification.get("differences")):
        if isinstance(item, dict):
            recorded.append(
                {
                    "field": _text(item.get("field")),
                    "candidate": _text(item.get("candidate")),
                    "final": _text(item.get("final")),
                }
            )
    if recorded:
        return recorded
    candidate = _dict(trace).get("candidate")
    if not isinstance(candidate, dict):
        return []
    row = final_row(trace)
    differences = []
    for field in FINAL_FIELDS:
        before = _text(candidate.get(field))
        after = row.get(field, "")
        if before != after:
            differences.append({"field": field, "candidate": before, "final": after})
    return differences


def summarise(trace) -> dict:
    """Everything the viewer and the TUI need, already redacted and typed."""
    payload = _dict(trace)
    verification = _dict(payload.get("verification"))
    row = final_row(payload)
    return {
        "request_id": _text(payload.get("request_id")),
        "run_id": _text(payload.get("run_id")),
        "provider": _text(payload.get("provider")) or "none",
        "model": _text(payload.get("model")) or "none",
        "prompt_version": _text(payload.get("prompt_version")),
        "prompt_hash": _text(payload.get("prompt_hash")),
        "model_calls": model_calls(payload),
        "tokens": token_total(payload),
        "status": row["affordability_status"],
        "method": row["recommended_payment_method"],
        "final_row": row,
        "usage": dict(_dict(payload.get("usage"))),
        "fallback_reason": (
            _text(payload.get("fallback_reason"))
            or _text(verification.get("fallback_reason"))
        ),
        "trace_error": _text(payload.get("trace_error")),
        "candidate": payload.get("candidate") if isinstance(payload.get("candidate"), dict) else None,
        "candidate_valid": bool(verification.get("candidate_valid")),
        "agreement": bool(verification.get("agent_agreement")),
        "rationale_source": _text(verification.get("rationale_source")) or "kernel",
        "differences": candidate_differences(payload),
        "tool_names": [_text(call.get("name")) for call in tool_calls(payload)],
        "retrieval": _dict(payload.get("retrieval")),
        "created_at": _text(_dict(payload.get("metadata")).get("created_at")),
    }


# ---------------------------------------------------------------------------
# HTML fragments
# ---------------------------------------------------------------------------


def _badge(status) -> str:
    text = _text(status)
    css = STATUS_CLASSES.get(text, "unknown")
    return f'<span class="badge {css}">{_esc(text or "unknown")}</span>'


def _kv_table(rows) -> str:
    body = "".join(
        f"<tr><th>{_esc(label)}</th><td>{value}</td></tr>" for label, value in rows
    )
    return f'<table class="kv"><tbody>{body}</tbody></table>'


def _grid_table(headers, rows) -> str:
    head = "".join(f"<th>{_esc(header)}</th>" for header in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _page(title, body, *, footer_note=None) -> str:
    note = f"<footer>{footer_note}</footer>" if footer_note else ""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n<style>\n{_INDEX_CSS}</style>\n"
        f"</head>\n<body>\n{body}\n{note}\n</body>\n</html>\n"
    )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tokens_text(summary) -> str:
    usage = _dict(summary.get("usage"))
    total = summary.get("tokens")
    if total is None:
        return "not recorded"
    parts = []
    if _text(usage.get("input_tokens")):
        parts.append(f"in {usage.get('input_tokens')}")
    if _text(usage.get("output_tokens")):
        parts.append(f"out {usage.get('output_tokens')}")
    return f"{total}" + (f" ({', '.join(parts)})" if parts else "")


def _or_note(value, note="n/a") -> str:
    """Escaped value, or a dim note when the trace does not carry it."""
    text = _esc(value)
    return text if text else f'<span class="note">{_esc(note)}</span>'


# ---------------------------------------------------------------------------
# Detail page
# ---------------------------------------------------------------------------


def render_detail(trace, *, source_path=None, generated=None) -> str:
    """Render one trace as a standalone HTML document."""
    summary = summarise(trace)
    row = summary["final_row"]
    request_id = summary["request_id"]
    status = summary["status"]
    method = summary["method"]
    differences = summary["differences"]
    retrieval = summary["retrieval"]

    header_rows = [
        ("request id", f"<code>{_esc(request_id)}</code>"),
        ("final status", _badge(status) if status else '<span class="note">not written</span>'),
        ("method", _esc(method) or '<span class="note">not written</span>'),
        (
            "provider / model",
            f"{_esc(summary['provider'])} / {_esc(summary['model'])}",
        ),
        (
            "prompt version / hash",
            f"{_or_note(summary['prompt_version'])} / "
            f"{_or_note(_clip(summary['prompt_hash'], 24))}",
        ),
        ("model calls", f'<span class="num">{summary["model_calls"]}</span>'),
        ("tokens", _esc(_tokens_text(summary))),
        ("fallback reason", _esc(summary["fallback_reason"]) or "none"),
        ("trace error", _esc(summary["trace_error"]) or "none"),
        ("run id", _esc(summary["run_id"]) or "unknown"),
        ("retrieval", _retrieval_text(retrieval)),
    ]
    if summary["created_at"]:
        header_rows.append(("recorded at", _esc(summary["created_at"])))
    if source_path is not None:
        header_rows.append(("source file", f"<code>{_esc(source_path)}</code>"))

    tool_rows = []
    for index, call in enumerate(tool_calls(trace), start=1):
        ok = bool(call.get("ok"))
        result = _esc(call.get("error")) if not ok else "ok"
        if not ok and not _text(call.get("error")):
            result = "failed"
        duration = call.get("duration_ms")
        tool_rows.append(
            (
                f'<span class="num">{index}</span>',
                f"<code>{_esc(_text(call.get('name')) or 'unknown')}</code>",
                f'<span class="{"ok" if ok else "bad"}">{"ok" if ok else "failed"}</span>',
                f"<code>{_esc(_json_summary(call.get('args')))}</code>",
                result,
                _esc(f"{duration} ms") if duration is not None else "",
            )
        )
    if not tool_rows:
        tool_rows = [("", '<span class="note">no tool calls were recorded</span>', "", "", "", "")]

    candidate_html = _candidate_block(summary)
    verification_html = _verification_block(summary)
    final_html = _grid_table(
        list(OUTPUT_FIELDS),
        [
            tuple(
                f'<span class="badge {STATUS_CLASSES.get(row[field], "")}">{_esc(row[field])}</span>'
                if field == "affordability_status" and row[field]
                else _esc(row[field]) or '<span class="note">empty</span>'
                for field in OUTPUT_FIELDS
            )
        ],
    )

    raw = json.dumps(trace, indent=2, sort_keys=True, ensure_ascii=False)
    if len(raw) > MAX_RAW_CHARS:
        raw = raw[:MAX_RAW_CHARS] + "\n... truncated for display ..."

    body = "\n".join(
        [
            f'<header><h1>Trace: {_esc(request_id)}</h1>'
            f'<p class="sub">run {_esc(summary["run_id"]) or "unknown"}'
            f" &middot; {_esc(_now() if generated is None else generated)}</p></header>",
            "<h2>Summary</h2>",
            _kv_table(header_rows),
            "<h2>Tool call timeline</h2>",
            _grid_table(
                ["#", "tool", "result", "arguments", "detail", "duration"], tool_rows
            ),
            "<h2>Candidate proposal</h2>",
            candidate_html,
            "<h2>Verification</h2>",
            verification_html,
            "<h2>Final enforced row (eight-column contract)</h2>",
            final_html,
            "<h2>Raw trace JSON</h2>",
            f"<details><summary>show the trace file ({_esc(len(raw))} chars)</summary>"
            f"<pre>{_esc(raw)}</pre></details>",
        ]
    )
    footer = (
        "Static, read-only page generated by <code>tools.trace_view</code> &middot; "
        "no server, no JavaScript, no external assets."
    )
    if differences:
        footer += f" &middot; {len(differences)} candidate/kernel difference(s) shown above."
    return _page(f"trace {request_id}", body, footer_note=footer)


def _retrieval_text(retrieval) -> str:
    if not retrieval:
        return '<span class="note">not recorded</span>'
    backend = _text(retrieval.get("backend")) or "none"
    queries = _text(retrieval.get("queries")) or "0"
    warnings = _list(retrieval.get("warnings"))
    text = f"{_esc(backend)} ({_esc(queries)} quer{'y' if queries == '1' else 'ies'})"
    if warnings:
        text += f' <span class="warn">{len(warnings)} warning(s): '
        text += _esc(_json_summary(warnings)) + "</span>"
    return text


def _candidate_block(summary) -> str:
    candidate = summary.get("candidate")
    if not isinstance(candidate, dict):
        return (
            '<p class="note">No candidate was proposed; the deterministic kernel row '
            "stands (faithful fallback).</p>"
        )
    valid = summary.get("candidate_valid")
    verdict = (
        '<span class="badge ok">valid</span>'
        if valid
        else '<span class="badge bad">invalid</span>'
    )
    rows = [("schema valid", verdict)]
    for field in FINAL_FIELDS:
        rows.append((field, f"<code>{_esc(candidate.get(field))}</code>"))
    rows.append(("evidence_sufficient", _esc(candidate.get("evidence_sufficient"))))
    rows.append(("confidence", _esc(candidate.get("confidence"))))
    rows.append(("rationale", _esc(candidate.get("rationale")) or '<span class="note">empty</span>'))
    return _kv_table(rows)


def _verification_block(summary) -> str:
    agreement = "yes" if summary.get("agreement") else "no"
    rows = [
        ("agent agreement", agreement),
        ("candidate valid", "yes" if summary.get("candidate_valid") else "no"),
        ("rationale source", _esc(summary.get("rationale_source"))),
        (
            "fallback reason",
            _esc(summary.get("fallback_reason")) or '<span class="note">none</span>',
        ),
    ]
    differences = summary.get("differences") or []
    block = [_kv_table(rows)]
    if differences:
        block.append("<h3>Differences (candidate &rarr; final)</h3>")
        block.append(
            _grid_table(
                ["field", "candidate", "final"],
                [
                    (
                        f"<code>{_esc(item.get('field'))}</code>",
                        _esc(item.get("candidate")) or '<span class="note">empty</span>',
                        _esc(item.get("final")) or '<span class="note">empty</span>',
                    )
                    for item in differences
                ],
            )
        )
    else:
        block.append(
            '<p class="note">No differences: the candidate matched the enforced row '
            "on every machine field.</p>"
        )
    return "\n".join(block)


# ---------------------------------------------------------------------------
# Index page
# ---------------------------------------------------------------------------


def page_name(request_id, used) -> str:
    """A collision-free detail page file name for one request id."""
    slug = _safe_name(request_id)
    if slug.lower() == "index":
        slug = f"{slug}-trace"
    candidate = f"{slug}.html"
    index = 2
    while candidate in used:
        candidate = f"{slug}-{index}.html"
        index += 1
    used.add(candidate)
    return candidate


def render_index(entries, *, links, run_id=None, source_dir=None, generated=None) -> str:
    """Render the index page linking to each detail page.

    ``links`` holds one href per entry, in the same order, so two runs that
    share a request id still link to their own page.
    """
    generated = _now() if generated is None else generated
    rows = []
    for position, entry in enumerate(entries[:MAX_INDEX_ROWS]):
        summary = summarise(entry.trace)
        href = _text(links[position]) if position < len(links) else ""
        rows.append(
            (
                f'<a href="{_esc(href)}">{_esc(summary["request_id"])}</a>',
                _badge(summary["status"]),
                _esc(summary["method"]) or '<span class="note">not written</span>',
                f"{_esc(summary['provider'])} / {_esc(summary['model'])}",
                str(summary["model_calls"]),
                _esc(_clip(summary["fallback_reason"], 140))
                or '<span class="note">none</span>',
                _esc(_clip(summary["trace_error"], 140))
                or '<span class="note">none</span>',
            )
        )
    truncated = max(len(entries) - len(rows), 0)
    table = _grid_table(
        [
            "request id",
            "final status",
            "method",
            "provider / model",
            "model calls",
            "fallback reason",
            "trace error",
        ],
        rows,
    )
    run_label = _text(run_id) or (
        _text(entries[0].trace.get("run_id")) if entries else ""
    )
    meta = _kv_table(
        [
            ("run id", _esc(run_label) or "unknown"),
            ("generated", _esc(generated)),
            ("trace count", str(len(entries))),
            (
                "source directory",
                f"<code>{_esc(source_dir)}</code>" if source_dir is not None
                else '<span class="note">not recorded</span>',
            ),
        ]
    )
    body = "\n".join(
        [
            f'<header><h1>Trace index: {_esc(run_label) or "unknown run"}</h1>'
            f'<p class="sub">{len(entries)} request trace(s)</p></header>',
            "<h2>Run</h2>",
            meta,
            "<h2>Requests</h2>",
            table,
        ]
        + (
            [f'<p class="note">{truncated} further trace(s) not listed.</p>']
            if truncated
            else []
        )
    )
    footer = (
        "Static, read-only index generated by <code>tools.trace_view</code> &middot; "
        "detail pages sit next to this file."
    )
    return _page(f"trace index {run_label}".strip(), body, footer_note=footer)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write(path, text) -> Path:
    target = Path(path)
    if target.exists() and target.is_dir():
        target = target / "index.html"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise TraceError(f"could not write {target}: {exc}") from None
    return target


def _one(args) -> int:
    trace = load_trace(args.trace)
    page = render_detail(trace, source_path=args.trace)
    if not args.out:
        sys.stdout.write(page)
        return 0
    target = _write(args.out, page)
    print(f"wrote {target}")
    return 0


def _directory(args) -> int:
    if not args.out:
        raise TraceError(
            "--trace-dir needs --out <index.html> (or a directory) so the detail "
            "pages have somewhere to live"
        )
    files = find_traces(args.trace_dir)
    entries = [TraceEntry(path=path, trace=load_trace(path)) for path in files]
    out_path = Path(args.out)
    if out_path.exists() and out_path.is_dir():
        index_path = out_path / "index.html"
        base = out_path
    else:
        index_path = out_path
        base = out_path.parent if str(out_path.parent) else Path(".")
    used: set[str] = {"index.html"}
    links = []
    written = []
    for entry in entries:
        request_id = _text(entry.trace.get("request_id"))
        href = page_name(request_id, used)
        links.append(href)
        written.append(_write(base / href, render_detail(entry.trace, source_path=entry.path)))
    run_id = ""
    for entry in entries:
        run_id = _text(entry.trace.get("run_id"))
        if run_id:
            break
    index_text = render_index(
        entries, links=links, run_id=run_id, source_dir=args.trace_dir
    )
    index_target = _write(index_path, index_text)
    print(f"wrote {index_target} and {len(written)} detail page(s) in {index_target.parent}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.trace_view",
        description=(
            "Render agent traces as static, read-only HTML (inline CSS, no server, "
            "no JavaScript, no external assets)."
        ),
        epilog=(
            "Examples:\n"
            "  python -m tools.trace_view --trace-dir traces/run --out traces/run/index.html\n"
            "  python -m tools.trace_view --trace traces/run/request_demo.json --out request_demo.html\n"
            "  python -m tools.trace_view --trace traces/run/request_demo.json  (HTML to stdout)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--trace", metavar="FILE", help="one trace JSON file")
    source.add_argument(
        "--trace-dir",
        metavar="DIR",
        help="a run directory (or a parent of run directories) of *.json traces",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        help=(
            "output HTML file; with --trace-dir this is the index page (an existing "
            "directory writes index.html inside it) and detail pages are written "
            "next to it"
        ),
    )
    args = parser.parse_args(argv)
    try:
        return _one(args) if args.trace else _directory(args)
    except TraceError as exc:
        print(f"trace_view: {exc}", file=sys.stderr)
        return 2


__all__ = [
    "DEFAULT_TRACE_DIR",
    "MAX_SUMMARY_CHARS",
    "OUTPUT_FIELDS",
    "REDACTED",
    "STATUS_CLASSES",
    "TraceEntry",
    "TraceError",
    "candidate_differences",
    "final_row",
    "find_trace",
    "find_traces",
    "load_trace",
    "main",
    "model_calls",
    "page_name",
    "render_detail",
    "render_index",
    "summarise",
    "token_total",
    "tool_calls",
]


if __name__ == "__main__":
    raise SystemExit(main())
