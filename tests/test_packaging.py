"""Packaging, configuration and Docker assertions (Task 7).

These are static, fast checks: no Docker daemon and no network are needed.  They
guard the three things a user must be able to trust from the repository alone:

* the shipped configuration is placeholder-only (no secret can be committed),
* the documented deployment path (Kilo / Nemotron Ultra) is discoverable from the
  environment template, the README and the provider module,
* the default remains offline and keyless, and the container can start without
  editing files.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import REPO_ROOT

SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9]{8,}", r"AKIA[0-9A-Z]{16}", r"ghp_[A-Za-z0-9]{8,}",
    r"xox[bpas]-[A-Za-z0-9-]+", r"AIza[0-9A-Za-z_-]{10,}",
    r"-----BEGIN .*PRIVATE KEY", r"aws_secret", r"Bearer [A-Za-z0-9._-]{8,}",
]

KILO_ENV_NAMES = ("BUY_WAIT_KILO_BASE_URL", "BUY_WAIT_KILO_API_KEY", "BUY_WAIT_KILO_MODEL")
KILO_DEFAULT_MODEL = "nemotron-3-ultra"

# Literal values allowed in `.env.example`; anything else must be `<...>`.
# Every entry here is a public, non-secret default that ships in the code anyway:
# a loopback URL, an enum, a timeout, a path under the repo, or an official model
# tag.  A real key or a remote endpoint stays a placeholder.
SAFE_ENV_VALUES = {
    "",
    "none",
    "ollama",
    "openrouter",
    "kilo",
    "auto",
    "50",
    "120",
    "180",
    "300",
    "60",
    "30",
    "0",
    "1",
    "2",
    "data/cache",
    "8192",
    "0.9",
    "40",
    "1.1",
    "1024",
    "qwen2.5:1.5b",
    "qwen3-vl:2b-instruct-q4_K_M",
    KILO_DEFAULT_MODEL,
    "http://localhost:11434",
    "http://127.0.0.1:11434",
    "jev-1.13-free",
    "https://opencode.ai/inference/systemone/v1/systemone",
}

ENV_ASSIGNMENT = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)=(.*)$", re.MULTILINE)


def _text(name: str) -> str:
    path = REPO_ROOT / name
    assert path.is_file(), f"{name} is missing"
    return path.read_text(encoding="utf-8")


def _assignments(text: str) -> dict[str, str]:
    """Every `NAME=value` in the template, whether commented out or not.

    Trailing inline comments (`  # note`) are stripped; a URL always follows the
    single-space `NAME=value` form, so it is left intact.
    """
    out: dict[str, str] = {}
    for name, value in ENV_ASSIGNMENT.findall(text):
        value = re.split(r"\s{2,}#", value, maxsplit=1)[0].strip()
        out[name] = value
    return out


# ---------------------------------------------------------------------------
# Configuration template
# ---------------------------------------------------------------------------

def test_env_example_documents_kilo_and_the_local_vision_names():
    text = _text(".env.example")
    for name in KILO_ENV_NAMES:
        assert f"{name}=" in text, f"{name} missing from .env.example"
    assert KILO_DEFAULT_MODEL in text
    # The local vision path and its cache are real code now, so their knobs must
    # be discoverable from the template rather than only from the source.
    for name in (
        "BUY_WAIT_OCR_BACKEND",
        "BUY_WAIT_VLM_BASE_URL",
        "BUY_WAIT_VLM_MODEL",
        "BUY_WAIT_VLM_TIMEOUT",
        "BUY_WAIT_VLM_ALLOW_REMOTE",
        "BUY_WAIT_DOCLING_PYTHON",
        "BUY_WAIT_DOCLING_TIMEOUT",
        "BUY_WAIT_CACHE_DIR",
        "BUY_WAIT_CACHE_TTL_DAYS",
        "BUY_WAIT_CACHE_DISABLE",
        "OPENCODE_API_KEY",
        "OPENCODE_AUTH_JSON",
        "BUY_WAIT_JEV_BASE_URL",
        "BUY_WAIT_JEV_MODEL",
        "BUY_WAIT_JEV_TIMEOUT",
    ):
        assert f"{name}=" in text, f"{name} missing from .env.example"


def test_no_env_file_is_shipped_or_trackable():
    import subprocess

    # A local `.env` is expected once someone follows the README; what must never
    # happen is committing it.  Check the index, not the filesystem, so a
    # developer's private configuration cannot fail the documented test command.
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert tracked.returncode != 0, ".env must never be committed (a local, untracked one is fine)"

    result = subprocess.run(
        # --no-index evaluates the ignore rules independently of the index, so the
        # check reports the rule rather than the current staging state.
        ["git", "check-ignore", "--no-index", ".env", ".env.local"], cwd=REPO_ROOT,
        capture_output=True, text=True,
    )
    assert result.returncode == 0, "`.env` must be gitignored"
    ignored = {line.strip() for line in result.stdout.splitlines()}
    assert {".env", ".env.local"} <= ignored


def test_local_generated_data_stays_out_of_git():
    import subprocess

    paths = ["data/cache/ocr/deadbeef.json", "data/app.db", "data/vector_index/x", "traces/run/x.json"]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", *paths], cwd=REPO_ROOT,
        capture_output=True, text=True,
    )
    ignored = {line.strip() for line in result.stdout.splitlines()}
    assert ignored == set(paths), f"generated state must be ignored, missing: {sorted(set(paths) - ignored)}"

def test_requirements_split_runtime_and_agent():
    base = _text("requirements.txt")
    for package in ("pandas", "rich", "pytest", "pytesseract", "Pillow"):
        assert re.search(rf"^{package}", base, re.IGNORECASE | re.MULTILINE), package
    agent = _text("requirements-agent.txt")
    assert "langchain-core" in agent and "langchain-openai" in agent
    # The deterministic path must not pull the model SDKs in.
    assert "langchain" not in base


# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------


def test_dockerfile_is_a_runnable_non_root_image():
    text = _text("Dockerfile")
    assert re.search(r"^FROM\s+python:3\.11-slim", text, re.MULTILINE), "expected python:3.11-slim"
    assert re.search(r"^WORKDIR\s+/app", text, re.MULTILINE), "image must run from /app"
    assert "requirements.txt" in text, "base deps must be installed"
    assert "requirements-agent.txt" in text, "the deployment target is the agentic app"
    assert re.search(r"^COPY\s+.*dataset", text, re.MULTILINE), "dataset/ must be in the image"

    users = re.findall(r"^USER\s+(\S+)", text, re.MULTILINE)
    assert users, "the container must drop root"
    assert all(user != "root" for user in users)
    assert "useradd" in text or "adduser" in text, "create the non-root user explicitly"

    launcher = re.search(r"^(ENTRYPOINT|CMD)\b.*$", text, re.MULTILINE | re.DOTALL)
    assert launcher, "the image needs a default command"
    assert "code.main" in launcher.group(0), "default entrypoint must be the documented CLI"
    assert not re.search(r"^COPY\s+\.env(\s|$)", text, re.MULTILINE), "never bake .env into the image"


def test_dockerignore_keeps_secrets_caches_and_outputs_out():
    patterns = [
        line.strip()
        for line in _text(".dockerignore").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    def covers(name: str) -> bool:
        return any(p == name or p.endswith("/" + name) for p in patterns)

    for required in (".git", ".venv", ".superpowers", ".opencode", ".env", "!.env.example",
                     "log.txt", "__pycache__", ".pytest_cache", "traces", "output.csv"):
        assert covers(required), f"{required} missing from .dockerignore"


# ---------------------------------------------------------------------------
# Docs and the offline default
# ---------------------------------------------------------------------------


def test_readme_documents_offline_default_and_kilo_deployment():
    raw = _text("README.md")
    text = raw.lower()
    for phrase in ("offline", "kilo", KILO_DEFAULT_MODEL, "requirements.txt",
                   "docker build", "docker run"):
        assert phrase.lower() in text, f"README missing {phrase!r}"
    for name in KILO_ENV_NAMES:
        assert name in raw, f"README missing {name}"
    assert "you must" in text, "README must say the user configures Kilo"


def test_readme_documents_the_local_image_readers_honestly():
    text = _text("README.md").lower()
    # The local vision path exists; the README must describe it *and* keep the
    # Tesseract fallback and the remaining accuracy limit in view.
    for phrase in ("qwen3-vl", "docling", "tesseract", "buy_wait_ocr_backend"):
        assert phrase in text, f"README missing {phrase!r} in the image-reading section"
    assert "resolve 15" in text or "15/16" in text, "README must state the measured OCR coverage"
    assert "unresolved" in text, "README must not imply every image resolves"


def test_default_remains_offline_and_keyless():
    from code.providers import PROVIDER_KINDS, build_provider

    provider, reason = build_provider({})
    assert provider is None, "no provider is configured by default"
    assert "BUY_WAIT_LLM_PROVIDER" in reason
    assert "none" in PROVIDER_KINDS
    assert "kilo" in PROVIDER_KINDS


def test_packaging_surface_has_no_secret_values():
    surface = [".env.example", "Dockerfile", ".dockerignore", "requirements.txt",
               "requirements-agent.txt", "README.md"]
    hits = []
    for name in surface:
        text = (REPO_ROOT / name).read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            if re.search(pattern, text):
                hits.append(f"{name}: {pattern}")
    assert hits == [], f"secret-like values in packaging surface: {hits}"
def test_every_env_example_value_is_a_public_placeholder():
    values = _assignments(_text(".env.example"))
    assert values, ".env.example has no assignments"
    unexpected = {
        name: value
        for name, value in values.items()
        if value not in SAFE_ENV_VALUES
        and not (value.startswith("<") and value.endswith(">"))
    }
    assert not unexpected, f"non-placeholder values in .env.example: {unexpected}"


def test_the_env_example_is_never_ignored():
    lines = [line.strip() for line in _text(".gitignore").splitlines()]
    ignores = [index for index, line in enumerate(lines) if line in {".env", ".env.*"}]
    negations = [index for index, line in enumerate(lines) if line == "!.env.example"]
    assert ignores, ".gitignore must ignore the real env files"
    assert negations, ".gitignore must re-include .env.example"
    # Order matters: a negation is only read after the pattern it undoes.
    assert min(negations) > max(ignores)
