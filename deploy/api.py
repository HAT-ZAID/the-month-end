"""Read-only HTTP surface over the deterministic kernel.

Two endpoints matter:

* ``GET /sample/{request_id}`` solves one of the 25 calibration requests live —
  the same kernel the desktop app uses, not a stored answer.
* ``POST /evaluate`` takes a balance, a floor, an amount and a date and returns
  the largest safe payment for a profile with no recorded events.

Nothing here mutates state and nothing is persisted: the API is a window onto
the kernel, not an account store. That is deliberate — Cloud Run/Fargate are
stateless, and a demo should not invent a database.

Set ``BUY_WAIT_API_TOKEN`` to require ``Authorization: Bearer <token>``.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

import pandas as pd

from code.evidence import load_pinned_amounts
from code.oracle import solve_safe_amount
from code.recurrence import detect_recurrence
from code.solver import solve_request

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "mock_dataset"

app = FastAPI(title="The Month-End kernel API", version="1.0")


class _Data:
    """Everything the kernel needs, loaded once."""

    def __init__(self) -> None:
        events = pd.read_csv(MOCK / "financial_events.csv", dtype=str, keep_default_na=False)
        self.events = events.to_dict("records")
        pinned = load_pinned_amounts()
        for event in self.events:
            key = str(event.get("event_id", "")).strip()
            if not str(event.get("amount", "")).strip() and key in pinned:
                event["amount"] = pinned[key]
        self.profiles = pd.read_csv(
            MOCK / "financial_profiles.csv", dtype=str, keep_default_na=False
        ).set_index("user_id")
        self.fx = pd.read_csv(MOCK / "exchange_rates.csv", dtype=str, keep_default_na=False).to_dict("records")
        self.sample = pd.read_csv(MOCK / "sample_requests.csv", dtype=str, keep_default_na=False)
        self.options = pd.read_csv(MOCK / "request_payment_options.csv", dtype=str, keep_default_na=False)
        self.messages = pd.read_csv(MOCK / "messages.csv", dtype=str, keep_default_na=False)
        self.flags = detect_recurrence(self.events)


_data: _Data | None = None


def data() -> _Data:
    global _data
    if _data is None:
        _data = _Data()
    return _data


def _authorise(authorization: str | None) -> None:
    expected = str(os.environ.get("BUY_WAIT_API_TOKEN") or "").strip()
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")


@app.get("/health")
def health(authorization: str | None = Header(default=None)) -> dict:
    _authorise(authorization)
    return {"status": "ok", "service": "buy-or-wait", "version": app.version}


@app.get("/sample")
def sample(authorization: str | None = Header(default=None)) -> dict:
    _authorise(authorization)
    rows = data().sample
    return {"count": len(rows), "request_ids": rows["request_id"].tolist()}


@app.get("/sample/{request_id}")
def sample_decision(request_id: str, authorization: str | None = Header(default=None)) -> dict:
    _authorise(authorization)
    frame = data()
    match = frame.sample[frame.sample["request_id"] == request_id]
    if match.empty:
        raise HTTPException(status_code=404, detail="unknown sample request")
    req = match.iloc[0]
    profile = frame.profiles.loc[req["user_id"]].to_dict()
    user_events = [event for event in frame.events if event["user_id"] == req["user_id"]]
    result = solve_request(
        req["request_id"],
        req["user_id"],
        req["request_date"],
        req["requested_amount"],
        profile,
        user_events,
        frame.fx,
        frame.options,
        frame.messages,
        req.get("desired_completion_date"),
        req.get("allows_partial_payment"),
        installment_safety="strict",
    )
    fields = (
        "amount_safe_to_pay",
        "affordability_status",
        "recommended_payment_method",
        "payment_plan",
        "earliest_date_for_full_payment",
        "spending_changes_needed",
        "decision_explanation",
    )
    return {
        "request_id": request_id,
        # An absent date is empty, never the string "None".
        **{field: ("" if result.get(field) is None else str(result.get(field))) for field in fields},
    }


class EvaluateRequest(BaseModel):
    balance: str = Field(..., description="current available balance, as a decimal string")
    floor: str = Field(..., description="minimum balance to keep, as a decimal string")
    amount: str = Field(..., description="the payment being considered")
    date: str = Field(..., description="ISO date the payment would happen")


#: The Kilo gateway serves a free pool with no key required. It may log prompts
#: and route to providers that train on them, so only kernel output is sent.
KILO_URL = os.environ.get("BUY_WAIT_KILO_URL") or "https://api.kilo.ai/api/gateway/chat/completions"
KILO_MODEL = os.environ.get("BUY_WAIT_KILO_MODEL") or "nvidia/nemotron-3-ultra-550b-a55b:free"
EXPLAIN_TIMEOUT_S = 20.0


def _figures(text: str) -> set[str]:
    """Every number in a sentence, normalised for comparison."""
    found = set()
    for token in re.findall(r"-?\d[\d,]*(?:\.\d+)?", str(text or "")):
        found.add(token.replace(",", "").rstrip("."))
    return found


def _explain(row: dict) -> tuple[str, str]:
    """Ask the free Kilo pool to say the kernel's answer in words.

    The prompt contains only the decision, never an account row, and the reply
    is accepted only when every figure in it already appears in the decision.
    """
    prompt = (
        "Rewrite this financial decision in two plain sentences for the person "
        "who asked. Do not invent any number, date or status that is not in the "
        "JSON. Do not give financial advice beyond it.\n\n"
        + json.dumps(row, ensure_ascii=False)
    )
    body = json.dumps(
        {
            "model": KILO_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2048,
            "temperature": 0.2,
            "stream": False,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        KILO_URL, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=EXPLAIN_TIMEOUT_S) as response:
        payload = json.loads(response.read().decode("utf-8"))
    choices = payload.get("choices") or [{}]
    message = (choices[0] or {}).get("message") or {}
    return str(message.get("content") or "").strip(), str(payload.get("model") or "")


@app.get("/explain/{request_id}")
def explain(request_id: str, authorization: str | None = Header(default=None)) -> dict:
    _authorise(authorization)
    row = sample_decision(request_id, authorization=authorization)
    allowed = _figures(" ".join(str(v) for v in row.values()))
    try:
        prose, model = _explain(row)
    except Exception as exc:  # network, timeout, malformed body
        raise HTTPException(status_code=503, detail=f"explainer unavailable: {type(exc).__name__}") from None
    if not prose:
        return {**row, "explanation": row["decision_explanation"], "explainer": None, "explainer_model": None}
    unsupported = _figures(prose) - allowed
    if unsupported:
        # Never show a figure the kernel did not produce.
        return {
            **row,
            "explanation": row["decision_explanation"],
            "explainer": None,
            "explainer_model": model,
            "rejected_figures": sorted(unsupported),
        }
    return {**row, "explanation": prose, "explainer": "kilo-free", "explainer_model": model}



@app.post("/evaluate")
def evaluate(body: EvaluateRequest, authorization: str | None = Header(default=None)) -> dict:
    _authorise(authorization)
    profile = {
        "home_currency": "USD",
        "current_available_balance": body.balance,
        "minimum_balance_to_keep": body.floor,
    }
    result = solve_safe_amount("api", body.date, body.amount, profile, [], [], set())
    safe = result["safe_amount"]
    return {
        "amount_safe_to_pay": str(safe),
        "affordable": str(safe) == str(Decimal(body.amount)),
        "fail_closed": result["fail_closed"],
        "fail_closed_reason": result["fail_closed_reason"],
    }
