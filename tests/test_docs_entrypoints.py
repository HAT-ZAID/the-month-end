"""The documented entry points must be the ones that exist.

``README.md`` is the first thing a reader runs, so a stale ``python -m ...``
line is an ImportError with the repository's blessing.  These tests read the
commands out of the documentation and prove every module they name resolves
without being executed, and pin the entry points the product actually ships so
a removed description fails as loudly as an invented one.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from tests.conftest import REPO_ROOT

README = REPO_ROOT / "README.md"
ARCHITECTURE = REPO_ROOT / "docs" / "architecture.md"
HANDOVER = REPO_ROOT / "docs" / "handover-state.md"

# Commands under ``python -m`` that are not repository modules.
EXTERNAL_COMMANDS = frozenset({"pip", "pytest", "unittest", "venv"})

# Every entry point the product ships.  A documented module missing from this
# set is fine (an experiment may be documented too); one of these missing from
# the README is a reader hitting a wall.
REQUIRED_ENTRY_POINTS = (
    "code.main",
    "evaluation.run",
    "tools.tui",
    "tools.app",
    "code.task_agent",
    "tools.trace_view",
    "tools.g2_probe",
    "tools.g2_model_mining",
    "tools.g2_transfer_study",
)

_MODULE = re.compile(r"python\s+-m\s+([A-Za-z_][\w.]*)")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _documented_modules(text: str) -> set[str]:
    found = {match.group(1) for match in _MODULE.finditer(text)}
    return found - EXTERNAL_COMMANDS


def test_readme_documents_every_shipped_entry_point():
    documented = _documented_modules(_text(README))
    missing = [name for name in REQUIRED_ENTRY_POINTS if name not in documented]
    assert not missing, (
        "README.md no longer documents these entry points: "
        + ", ".join(f"python -m {name}" for name in missing)
    )


def test_every_documented_module_entry_point_resolves():
    documented = _documented_modules(_text(README))
    assert documented, "README.md does not document a single python -m entry point"
    unresolved = []
    for name in sorted(documented):
        spec = importlib.util.find_spec(name)
        if spec is None or not spec.origin or not spec.origin.endswith(".py"):
            unresolved.append(name)
    assert not unresolved, (
        "README.md points at modules that do not resolve: "
        + ", ".join(f"python -m {name}" for name in unresolved)
    )


def test_architecture_names_the_same_entry_points():
    text = _text(ARCHITECTURE)
    missing = [name for name in REQUIRED_ENTRY_POINTS if f"python -m {name}" not in text]
    assert not missing, (
        "docs/architecture.md does not name: "
        + ", ".join(f"python -m {name}" for name in missing)
    )


def test_handover_does_not_promise_a_removed_forecast_flag():
    # The flag and the corrected arithmetic were both removed. A handover that
    # still tells the next maintainer to keep the correction behind it sends
    # them looking for a switch that no longer exists.
    flat = " ".join(_text(HANDOVER).split())
    assert "behind its flag" not in flat
    assert "correction remains removed" in flat
    assert "keeps G1 at 0/0/0" in flat


def test_readme_says_the_gate_leaves_output_csv_alone():
    flat = " ".join(_text(README).split())
    assert re.search(
        r"evaluation\.run[^.]{0,240}does not (?:rewrite|touch) `?output\.csv`?", flat
    ), (
        "README.md must say that `python -m evaluation.run --mode pr` does not "
        "rewrite output.csv"
    )
