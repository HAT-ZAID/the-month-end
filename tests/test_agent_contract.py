"""Contract tests for the Task 5A agent substrate.

Scope is deliberately narrow: schema validation, kernel ownership in the
verifier, trace redaction/serialization, replay fixtures, the deterministic
``run_agent_once`` path, and structured tool errors.  The kernel itself has its
own suite and is not re-tested here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from code.agent import run_agent_once
from code.providers import ReplayProvider
from code.schema import DecisionCandidate, parse_candidate, stable_json
from code.tools import DatasetBundle, Toolbox
from code.trace import REDACTED, TraceRecorder, deterministic_view
from code.verifier import enforce_decision

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "agent_replay"

KERNEL = {
    "request_id": "request_demo",
    "amount_safe_to_pay": "1000",
    "affordability_status": "affordable_now",
    "recommended_payment_method": "full_payment",
    "payment_plan": "2026-01-05:1000",
    "earliest_date_for_full_payment": "2026-01-05",
    "spending_changes_needed": "none",
    "decision_explanation": "Recommended full_payment (affordable_now).",
    "evidence": {},
}


def _candidate(**overrides) -> dict:
    payload = {
        "request_id": "request_demo",
        "amount_safe_to_pay": "1000.00",
        "affordability_status": "affordable_now",
        "recommended_payment_method": "full_payment",
        "payment_plan": "2026-01-05:1000.00",
        "earliest_date_for_full_payment": "2026-01-05",
        "spending_changes_needed": "none",
        "evidence_sufficient": True,
        "confidence": 0.9,
        "rationale": "The full amount fits above the protected minimum balance.",
    }
    payload.update(overrides)
    return payload


def _empty_frame(columns) -> pd.DataFrame:
    return pd.DataFrame(columns=list(columns))


def _bundle() -> DatasetBundle:
    requests = pd.DataFrame(
        [
            {
                "request_id": "request_demo",
                "user_id": "user_demo",
                "request_date": "2026-01-05",
                "request_type": "purchase",
                "requested_amount": "1000",
                "desired_completion_date": "2026-01-31",
                "allows_partial_payment": "false",
                "request_text": "",
            }
        ]
    )
    profiles = pd.DataFrame(
        [
            {
                "user_id": "user_demo",
                "home_currency": "USD",
                "current_available_balance": "5000",
                "minimum_balance_to_keep": "2000",
                "financial_priorities": "",
                "expense_categories_to_protect": "",
                "expense_categories_user_is_willing_to_reduce": "",
                "expense_categories_user_is_willing_to_stop": "",
                "payment_methods_user_will_consider": "full_payment",
                "max_installment_months": "",
            }
        ]
    )
    options = _empty_frame(
        [
            "payment_option_id",
            "request_id",
            "payment_method",
            "payment_amount",
            "number_of_payments",
            "first_payment_date",
            "payment_frequency_days",
            "financing_fee",
            "total_payable_amount",
        ]
    )
    messages = _empty_frame(
        [
            "message_id",
            "user_id",
            "request_id",
            "related_event_id",
            "sent_at",
            "source_type",
            "message_text",
        ]
    )
    return DatasetBundle.from_frames(
        requests=requests,
        profiles=profiles,
        events=(),
        fx=(),
        options=options,
        messages=messages,
    )


def test_schema_accepts_valid_candidate_and_rejects_missing_field():
    candidate, errors = parse_candidate(_candidate())
    assert candidate is not None
    assert errors == []

    broken = _candidate()
    del broken["confidence"]
    candidate, errors = parse_candidate(broken)
    assert candidate is None
    assert any("confidence" in message for message in errors)


def test_schema_rejects_conflicting_and_excess_spending_changes():
    conflict = _candidate(spending_changes_needed="stop:event_01|reduce_to:event_01:5")
    candidate, errors = parse_candidate(conflict)
    assert candidate is None
    assert any("event_01" in message for message in errors)

    four = _candidate(
        spending_changes_needed="stop:event_01|stop:event_02|stop:event_03|stop:event_04"
    )
    candidate, errors = parse_candidate(four)
    assert candidate is None
    assert any("at most 3" in message for message in errors)


def test_insufficient_evidence_shape_requires_the_flag_and_the_shape():
    insufficient = _candidate(
        amount_safe_to_pay="0",
        affordability_status="not_affordable",
        recommended_payment_method="not_recommended",
        payment_plan="none",
        earliest_date_for_full_payment=None,
        spending_changes_needed="none",
        evidence_sufficient=False,
        rationale="Missing the employer payslip that confirms the next salary amount.",
    )
    candidate, errors = parse_candidate(insufficient)
    assert candidate is not None, errors

    lying = dict(insufficient, amount_safe_to_pay="500")
    candidate, errors = parse_candidate(lying)
    assert candidate is None
    assert any("not-enough-evidence" in message for message in errors)

    normal = _candidate(evidence_sufficient=False)
    candidate, errors = parse_candidate(normal)
    assert candidate is None
    assert any("not-enough-evidence" in message for message in errors)


def test_verifier_returns_kernel_values_when_candidate_lies():
    kernel = dict(KERNEL)
    liar = _candidate(
        amount_safe_to_pay="5000",
        affordability_status="affordable_later",
        recommended_payment_method="wait",
        payment_plan="2026-02-01:5000",
        earliest_date_for_full_payment="2026-02-01",
    )
    result = enforce_decision(liar, kernel)

    row = result["final_row"]
    verification = result["verification"]
    assert row["amount_safe_to_pay"] == "1000"
    assert row["affordability_status"] == "affordable_now"
    assert row["recommended_payment_method"] == "full_payment"
    assert row["payment_plan"] == "2026-01-05:1000"
    assert row["earliest_date_for_full_payment"] == "2026-01-05"
    # The candidate was wrong about every plan field, so its prose must not ship
    # even though the claim check found nothing it could contradict: agreement
    # is the first gate and claim checking only narrows it further.
    assert row["decision_explanation"] == KERNEL["decision_explanation"]
    assert verification["rationale_source"] == "kernel"
    assert verification["claim_check"]["unsupported"] == []

    assert verification["agent_agreement"] is False
    assert verification["candidate_valid"] is True
    assert verification["fallback_reason"]
    assert {
        "amount_safe_to_pay",
        "affordability_status",
        "recommended_payment_method",
        "payment_plan",
        "earliest_date_for_full_payment",
    } <= {item["field"] for item in verification["differences"]}
    assert kernel == KERNEL


def test_verifier_records_agreement_when_candidate_matches():
    result = enforce_decision(_candidate(), dict(KERNEL))

    verification = result["verification"]
    assert verification["agent_agreement"] is True
    assert verification["differences"] == []
    assert verification["candidate_valid"] is True
    assert verification["fallback_reason"] is None
    assert verification["rationale_source"] == "candidate"
    assert result["final_row"]["decision_explanation"] == _candidate()["rationale"]


def test_trace_redacts_secrets_and_writes_stable_json(tmp_path):
    def record(duration_ms):
        recorder = TraceRecorder("run-1", tmp_path / f"trace-{duration_ms}")
        recorder.begin("request_demo")
        recorder.record_tool_call(
            "get_request_context",
            {
                "request_id": "request_demo",
                "api_key": "sk-live-should-not-leak",
                "nested": {"Authorization": "Bearer should-not-leak"},
            },
            ok=True,
            error=None,
            duration_ms=duration_ms,
        )
        path = recorder.finish(
            request_id="request_demo",
            provider="replay",
            model="replay-fixture",
            prompt_version="v1",
            prompt_hash="abcdef",
            candidate=_candidate(),
            verification={"agent_agreement": True},
            final_row=dict(KERNEL),
            usage={"model_calls": 0},
            fallback_reason=None,
        )
        return Path(path)

    path_a = record(0.5)
    path_b = record(9.0)
    text = path_a.read_text(encoding="utf-8")
    payload = json.loads(text)

    # Same bytes as the canonical serializer: sorted keys, fixed formatting.
    assert text == stable_json(payload) + "\n"
    args = payload["tool_calls"][0]["args"]
    assert args["api_key"] == REDACTED
    assert args["nested"]["Authorization"] == REDACTED
    assert args["request_id"] == "request_demo"
    assert "sk-live-should-not-leak" not in text

    # Wall-clock metadata is the only thing that differs between runs.
    assert deterministic_view(payload) == deterministic_view(
        json.loads(path_b.read_text(encoding="utf-8"))
    )
    # ``duration_ms`` is real elapsed time and two back-to-back writes can land
    # on the same millisecond, so assert it is recorded and numeric rather than
    # asserting the two runs always differ (that assertion was flaky).
    assert isinstance(payload["metadata"]["duration_ms"], (int, float))
    assert payload["metadata"]["duration_ms"] >= 0


def test_replay_provider_reads_fixture_and_returns_none_when_missing(tmp_path):
    provider = ReplayProvider(FIXTURES)
    candidate = provider.propose({"request_id": "request_demo"}, None, None)
    assert isinstance(candidate, DecisionCandidate)
    assert candidate.request_id == "request_demo"
    assert provider.propose({"request_id": "request_missing"}, None, None) is None
    assert provider.propose({"request": {"request_id": "request_demo"}}, None, None) is not None

    empty_dir = ReplayProvider(tmp_path)
    assert empty_dir.propose({"request_id": "request_demo"}, None, None) is None

    (tmp_path / "candidates.json").write_text("{not json", encoding="utf-8")
    malformed = ReplayProvider(tmp_path)
    assert malformed.propose({"request_id": "request_demo"}, None, None) is None


def test_run_agent_once_without_provider_returns_kernel_row():
    kernel = dict(KERNEL)
    outcome = run_agent_once(
        {"request_id": "request_demo"}, provider=None, tools=None, trace=None,
        kernel_result=kernel,
    )

    assert outcome["trace_path"] is None
    for field in (
        "request_id",
        "amount_safe_to_pay",
        "affordability_status",
        "recommended_payment_method",
        "payment_plan",
        "earliest_date_for_full_payment",
        "spending_changes_needed",
    ):
        assert outcome["row"][field] == kernel[field]
    assert outcome["verification"]["agent_agreement"] is False
    assert outcome["verification"]["fallback_reason"]


def test_tool_errors_are_structured_and_never_raise():
    toolbox = Toolbox(data=_bundle())

    missing = toolbox.call("get_request_context", request_id="request_missing")
    assert missing["ok"] is False
    assert missing["fatal"] is False
    assert "request_missing" in missing["error"]

    unknown = toolbox.call("not_a_tool", request_id="request_demo")
    assert unknown["ok"] is False
    assert unknown["fatal"] is False

    bad_arguments = toolbox.call("get_financial_events")
    assert bad_arguments["ok"] is False
    assert bad_arguments["fatal"] is False


class _ExplodingTrace:
    """Trace recorder whose write step always fails (disk full / NaN)."""

    enabled = True

    def begin(self, request_id):
        return None

    def record(self, kind, payload=None):
        return None

    def finish(self, **kwargs):
        raise OSError("disk full while writing trace")


def _write_tiny_dataset(root: Path, request_id: str = "request_demo") -> None:
    dataset = root / "dataset"
    dataset.mkdir(parents=True, exist_ok=True)
    (dataset / "requests.csv").write_text(
        "request_id,user_id,request_date,request_type,requested_amount,"
        "desired_completion_date,allows_partial_payment,request_text\n"
        f"{request_id},user_demo,2026-01-05,purchase,1000,2026-01-31,false,"
        "Is 1000 safe today?\n",
        encoding="utf-8",
    )
    (dataset / "financial_profiles.csv").write_text(
        "user_id,home_currency,current_available_balance,minimum_balance_to_keep,"
        "financial_priorities,expense_categories_to_protect,"
        "expense_categories_user_is_willing_to_reduce,"
        "expense_categories_user_is_willing_to_stop,"
        "payment_methods_user_will_consider,max_installment_months\n"
        "user_demo,USD,5000,2000,,,,,full_payment,\n",
        encoding="utf-8",
    )
    (dataset / "financial_events.csv").write_text(
        "event_id,user_id,event_type,description,category,direction,amount,currency,"
        "event_date,settlement_date,status,linked_event_id,flexibility,"
        "minimum_allowed_amount\n",
        encoding="utf-8",
    )
    (dataset / "exchange_rates.csv").write_text(
        "rate_date,from_currency,to_currency,rate\n", encoding="utf-8"
    )
    (dataset / "request_payment_options.csv").write_text(
        "payment_option_id,request_id,payment_method,payment_amount,"
        "number_of_payments,first_payment_date,payment_frequency_days,financing_fee,"
        "total_payable_amount\n"
        f"payment_option_01,{request_id},full_payment,1000,1,2026-01-05,,0,1000\n",
        encoding="utf-8",
    )
    (dataset / "messages.csv").write_text(
        "message_id,user_id,request_id,related_event_id,sent_at,source_type,message_text\n",
        encoding="utf-8",
    )
    (dataset / "images.csv").write_text(
        "image_id,user_id,request_id,related_event_id\n", encoding="utf-8"
    )


def test_agent_failure_preserves_kernel_row_and_still_writes_output(
    tmp_path, monkeypatch
):
    import code.agent as agent_mod
    from code.main import run_pipeline

    _write_tiny_dataset(tmp_path)
    plain, _ = run_pipeline(repo_root=tmp_path, write_output=False, write_usage=False)

    def boom(*args, **kwargs):
        raise RuntimeError("verifier exploded")

    monkeypatch.setattr(agent_mod, "enforce_decision", boom)
    output = tmp_path / "agent_out.csv"
    agent_run, _ = run_pipeline(
        repo_root=tmp_path,
        output_path=output,
        write_output=True,
        write_usage=False,
        agent_request_ids=["request_demo"],
    )

    assert output.is_file()
    written = pd.read_csv(output, dtype=str, keep_default_na=False)
    assert len(written) == 1
    assert agent_run.equals(plain)
    for column in (
        "request_id",
        "amount_safe_to_pay",
        "affordability_status",
        "recommended_payment_method",
        "payment_plan",
        "earliest_date_for_full_payment",
        "spending_changes_needed",
        "decision_explanation",
    ):
        assert written.loc[0, column] == plain.loc[0, column]


def test_trace_write_failure_keeps_final_row():
    outcome = run_agent_once(
        {"request_id": "request_demo"},
        provider=None,
        tools=None,
        trace=_ExplodingTrace(),
        kernel_result=dict(KERNEL),
    )

    assert outcome["trace_path"] is None
    assert "trace_error" in outcome
    assert "disk full" in outcome["trace_error"]
    assert outcome["row"]["amount_safe_to_pay"] == "1000"
    assert outcome["row"]["affordability_status"] == "affordable_now"


def test_trace_redacts_secret_names_but_keeps_token_counters(tmp_path):
    recorder = TraceRecorder("run-redaction", tmp_path)
    recorder.begin("request_demo")
    path = recorder.finish(
        request_id="request_demo",
        provider="null",
        model="none",
        usage={
            "total_tokens": 5,
            "input_tokens": 7,
            "output_tokens": 3,
            "api_key": "sk-live-should-not-leak",
            "access_token": "token-should-not-leak",
            "Authorization": "Bearer should-not-leak",
        },
    )
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    usage = payload["usage"]

    assert usage["total_tokens"] == 5
    assert usage["input_tokens"] == 7
    assert usage["output_tokens"] == 3
    assert usage["api_key"] == REDACTED
    assert usage["access_token"] == REDACTED
    assert usage["Authorization"] == REDACTED
    text = Path(path).read_text(encoding="utf-8")
    assert "should-not-leak" not in text


def test_context_mismatch_forces_kernel_rationale():
    result = enforce_decision(
        _candidate(), dict(KERNEL), request_context={"request_id": "request_other"}
    )

    verification = result["verification"]
    assert verification["agent_agreement"] is True
    assert verification["rationale_source"] == "kernel"
    assert result["final_row"]["decision_explanation"] == KERNEL["decision_explanation"]
    assert "request_other" in verification["fallback_reason"]


def test_the_claim_check_sees_the_cents_the_csv_ships():
    from decimal import Decimal

    from code.claims import verify_claims
    from code.verifier import _kernel_row

    kernel = {field: "" for field in (
        "request_id", "affordability_status", "recommended_payment_method",
        "payment_plan", "earliest_date_for_full_payment", "spending_changes_needed",
    )}
    kernel.update(
        {
            "request_id": "request_31",
            "amount_safe_to_pay": Decimal("642986.74150000000000000000"),
            "affordability_status": "affordable_now",
            "recommended_payment_method": "full_payment",
            "payment_plan": "none",
            "earliest_date_for_full_payment": "2025-08-03",
            "spending_changes_needed": "none",
            "decision_explanation": "",
        }
    )
    row = _kernel_row(kernel)
    # The figure the tool showed the model, and the figure output.csv holds.
    assert verify_claims("Up to 642986.74 is safe today.", row)["checked"] >= 1
    assert verify_claims("Up to 642986.74 is safe today.", row)["ok"] is True
    # A figure the CSV never contains is not grounded.
    assert verify_claims("Up to 642986.7415 is safe today.", row)["ok"] is False
