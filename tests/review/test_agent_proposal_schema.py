"""Contract tests for the agent's proposal surface and local sampling settings.

Two regressions are guarded here.

1. ``propose_decision`` must expose the ten contract fields **flat**.  A nested
   ``{"candidate": {...}}`` wrapper made a small local model fill only the six
   fields it could anchor (enums, booleans, fixed strings) and drop
   ``request_id``, ``amount_safe_to_pay``, ``confidence`` and ``rationale``, so
   every proposal was rejected by the deterministic parser.
2. The local Ollama sampling options must stay explicit and reproducible:
   temperature 0 by default, with every other knob coming from the environment.
"""
from __future__ import annotations

import pytest

from code.agent import (
    CANDIDATE_FIELDS,
    CANDIDATE_SCHEMA,
    PROMPT_VERSION,
    TOOL_SCHEMAS,
    prompt_hash,
)
from code.providers import (
    DEFAULT_NUM_CTX,
    OLLAMA_NUM_CTX_ENV,
    OLLAMA_NUM_PREDICT_ENV,
    OLLAMA_REPEAT_PENALTY_ENV,
    OLLAMA_TEMPERATURE_ENV,
    OLLAMA_TOP_K_ENV,
    OLLAMA_TOP_P_ENV,
    OllamaProvider,
)


def _proposal_parameters() -> dict:
    return TOOL_SCHEMAS["propose_decision"]["parameters"]


def test_propose_decision_takes_the_contract_fields_flat() -> None:
    """The ten fields are top-level arguments, not a nested object."""
    parameters = _proposal_parameters()
    assert "candidate" not in parameters["properties"]
    assert set(parameters["properties"]) == set(CANDIDATE_FIELDS)
    assert list(parameters["required"]) == list(CANDIDATE_FIELDS)
    assert parameters["properties"] == CANDIDATE_SCHEMA["properties"]


def test_every_proposal_field_is_described() -> None:
    """A field with no description is a field a small model silently omits."""
    undocumented = [
        name
        for name, spec in CANDIDATE_SCHEMA["properties"].items()
        if not str(spec.get("description") or "").strip()
    ]
    assert undocumented == []


def test_propose_decision_description_demands_all_ten_keys() -> None:
    description = TOOL_SCHEMAS["propose_decision"]["description"]
    for field in CANDIDATE_FIELDS:
        assert field in description
    assert "required" in description.lower()


def test_ollama_defaults_are_reproducible() -> None:
    provider = OllamaProvider("gemma4:e2b-it-qat", env={})
    options = provider.generation_options()
    assert options["temperature"] == 0.0
    assert options["num_ctx"] == DEFAULT_NUM_CTX
    assert "top_p" not in options
    assert "top_k" not in options
    assert "repeat_penalty" not in options
    assert "num_predict" not in options


def test_ollama_generation_options_come_from_the_environment() -> None:
    env = {
        OLLAMA_TEMPERATURE_ENV: "0.3",
        OLLAMA_NUM_CTX_ENV: "4096",
        OLLAMA_NUM_PREDICT_ENV: "512",
        OLLAMA_TOP_P_ENV: "0.9",
        OLLAMA_TOP_K_ENV: "40",
        OLLAMA_REPEAT_PENALTY_ENV: "1.05",
    }
    options = OllamaProvider("gemma4:e2b-it-qat", env=env).generation_options()
    assert options == {
        "temperature": 0.3,
        "num_ctx": 4096,
        "num_predict": 512,
        "top_p": 0.9,
        "top_k": 40,
        "repeat_penalty": 1.05,
    }


@pytest.mark.parametrize(
    "key, value",
    [
        (OLLAMA_TEMPERATURE_ENV, "warm"),
        (OLLAMA_NUM_CTX_ENV, ""),
        (OLLAMA_NUM_CTX_ENV, "abc"),
        (OLLAMA_TOP_P_ENV, "nan-ish"),
        (OLLAMA_TOP_K_ENV, "many"),
        (OLLAMA_REPEAT_PENALTY_ENV, "high"),
        (OLLAMA_NUM_PREDICT_ENV, "lots"),
    ],
)
def test_bad_generation_settings_fall_back_instead_of_raising(key: str, value: str) -> None:
    options = OllamaProvider("gemma4:e2b-it-qat", env={key: value}).generation_options()
    assert options["temperature"] == 0.0
    assert options["num_ctx"] == DEFAULT_NUM_CTX


def test_context_window_is_never_degenerate() -> None:
    options = OllamaProvider("gemma4:e2b-it-qat", env={OLLAMA_NUM_CTX_ENV: "16"}).generation_options()
    assert options["num_ctx"] >= 512


def test_prompt_version_names_the_flat_proposal_contract() -> None:
    assert PROMPT_VERSION == "agent-loop-v2"


def test_prompt_hash_tracks_the_contract_not_just_the_label(monkeypatch) -> None:
    baseline = prompt_hash()
    assert baseline == prompt_hash(), "the digest must be stable across calls"

    monkeypatch.setattr("code.agent.CANDIDATE_FIELDS", tuple(CANDIDATE_FIELDS)[:-1])
    assert prompt_hash() != baseline, "dropping a contract field must change the hash"
    monkeypatch.undo()

    monkeypatch.setattr("code.agent.CANDIDATE_SCHEMA", {})
    assert prompt_hash() != baseline, "changing the schema must change the hash"
    monkeypatch.undo()

    assert prompt_hash() == baseline

