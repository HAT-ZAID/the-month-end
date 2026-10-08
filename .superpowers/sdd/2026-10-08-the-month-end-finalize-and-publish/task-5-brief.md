## Task 5: Schema-checked LLM explanation contract

**Files:**
- Modify: `code/explanations.py`
- Test: `tests/test_llm_explanations.py`

**Interfaces:**
- Consumes: unchanged `generate_explanation(packet, call=None) -> str`, `_configured_call(packet) -> dict`.
- Produces: a strict JSON schema; non-conforming output falls back deterministically.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_llm_explanations.py`:

```python
def test_provider_must_return_schema_fields_or_fallback():
    usage = new_usage()
    result = generate_explanation(
        _packet(_usage=usage),
        call=lambda _: {"content": '{"note":"looks fine to me"}'},
    )
    assert "partial_payment" in result
    assert usage["rejected"] == 1


def test_provider_insufficient_evidence_is_accepted_when_grounded():
    usage = new_usage()
    result = generate_explanation(
        _packet(
            _usage=usage,
            amount_safe_to_pay="0",
            recommended_payment_method="not_recommended",
            evidence_warnings=[{"event_id": "event_01", "flag": "AMOUNT_UNRESOLVED"}],
        ),
        call=lambda _: {"content": json.dumps({
            "decision_explanation": (
                "Not recommended: evidence for event_01 is unresolved, so safety "
                "cannot be verified while preserving the minimum balance."
            ),
            "evidence_sufficient": False,
            "missing_evidence": ["event_01"],
            "supporting_facts": ["amount_safe_to_pay=0", "minimum_balance=200"],
        })},
    )
    assert "unresolved" in result.lower()
    assert usage["successful"] == 1
```

- [ ] **Step 2: Run to verify RED**

```powershell
python -m pytest tests/test_llm_explanations.py -q
```

Expected: the first test fails (current parser accepts a single-key object).

- [ ] **Step 3: Enforce the schema**

In `code/explanations.py`:

```python
_REQUIRED_KEYS = ("decision_explanation", "evidence_sufficient", "supporting_facts")
_OPTIONAL_KEYS = ("missing_evidence",)


def _schema_ok(parsed: dict) -> bool:
    if not isinstance(parsed, dict):
        return False
    if any(k not in parsed for k in _REQUIRED_KEYS):
        return False
    if any(k not in _REQUIRED_KEYS + _OPTIONAL_KEYS for k in parsed):
        return False
    if not isinstance(parsed["evidence_sufficient"], bool):
        return False
    if not isinstance(parsed["supporting_facts"], list):
        return False
    return isinstance(parsed["decision_explanation"], str)
```

Apply the strict schema to structured provider metadata. Keep the legacy single-key leniency only for injected string callbacks so the 15 pre-existing tests stay green. Tighten the system prompt to name the exact keys and require `evidence_sufficient: false` whenever `missing_evidence` is non-empty.

- [ ] **Step 4: Verify GREEN and the call budget**

```powershell
$env:BUY_WAIT_LLM_MAX_CALLS='1'
python -m pytest tests/test_llm_explanations.py -q
```

Expected: all pass; live spend stays zero because tests inject their own call.

---

