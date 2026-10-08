"""Evidence handling expectations, grounded in dataset facts (pre-build).

What this program checks (problem_statement :169-172, :200-207; AGENTS 6.1, 6.3):
- The 9 verified message templates exist with their exact wording, so the future
  solution's amend / cancel / delay / confirm parsing has real fixtures to hit.
- Blank related_event_id means "no single event row", NOT "irrelevant".
- Every one of the 16 blank-amount events resolves to an existing PNG file.
- Locale routing expectations (Indonesian dot-thousands, Spanish safety question).
- Conflict-order and no-invent rules are documented here as data facts the
  solution must later honor (behavioral tests land once the solver exists).
"""


def test_amend_payroll_date_template_present(messages_df):
    """Payroll date replacement must be detectable (msg_05 pattern: replaces earlier date)."""
    m = messages_df[messages_df["message_id"] == "message_05"]
    assert len(m) == 1
    text = m.iloc[0]["message_text"]
    assert "replaces the payroll date" in text and "2024-09-23" in text


def test_bonus_hold_and_contract_end_templates_present(messages_df):
    """Unapproved bonus (msg_48) and ended contract (msg_09) must be excludable as income."""
    assert "not been approved" in messages_df[messages_df["message_id"] == "message_48"].iloc[0]["message_text"]
    assert "has ended" in messages_df[messages_df["message_id"] == "message_09"].iloc[0]["message_text"]


def test_refund_initiated_vs_settled_language_present(messages_df):
    """'Initiated but not reached' (pending, excluded) must differ from settled/confirmed wording."""
    m14 = messages_df[messages_df["message_id"] == "message_14"].iloc[0]["message_text"]
    assert "has not reached your account yet" in m14
    m64 = messages_df[messages_df["message_id"] == "message_64"].iloc[0]["message_text"]
    assert "receipt has the final amount" in m64


def test_failed_retry_template_present(messages_df):
    """Failed debit with scheduled retry (msg_69) keeps the bill reserved, counted once."""
    m = messages_df[messages_df["message_id"] == "message_69"].iloc[0]["message_text"]
    assert "failed" in m and "another debit" in m


def test_blank_related_event_means_no_single_row_not_irrelevant(messages_df):
    """176/215 messages have blank related_event_id: no 1-1 event row exists (AGENTS 6.1),
    but user/request-scoped rows (e.g. payroll shifts) still move the forecast."""
    blank = messages_df[messages_df["related_event_id"] == ""]
    assert len(blank) == 176
    assert (blank["user_id"] != "").all()


def test_all_16_blank_amounts_have_pngs(images_df, events_df):
    """The 16 must-OCR events each resolve to dataset/media/images/<image_id>.png (never zero)."""
    from pathlib import Path
    from tests.conftest import MEDIA
    assert len(images_df) == 16
    blanks = set(events_df[events_df["amount"] == ""]["event_id"])
    assert set(images_df["related_event_id"]) == blanks
    for _, r in images_df.iterrows():
        p = MEDIA / f"{r['image_id']}.png"
        assert p.exists(), f"missing image {p}"
        assert p.stat().st_size > 0, f"empty image {p}"


def test_indonesian_and_spanish_rows_present(requests_df):
    """Multilingual intent must be classifiable (req_43 Indonesian, req_117 Spanish)."""
    r43 = requests_df[requests_df["request_id"] == "request_43"].iloc[0]["request_text"]
    assert "43.339.000" in r43  # dot-thousands = 43,339,000 IDR
    r117 = requests_df[requests_df["request_id"] == "request_117"].iloc[0]["request_text"]
    assert "880" in r117


def test_text_asking_partial_despite_flag_false(requests_df):
    """req_26 asks 'send part' with allows_partial_payment=false: text intent must NEVER
    override the flag (partial gate stays closed)."""
    r26 = requests_df[requests_df["request_id"] == "request_26"].iloc[0]
    assert r26["allows_partial_payment"].strip().lower() == "false"
    assert "send part" in r26["request_text"]
