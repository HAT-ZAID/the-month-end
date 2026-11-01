"""Where the calibration fixture lives.

The fixture used to be called ``dataset/``.  That name kept outliving the
competition it was collected for, and it invited the mistake the interface kept
making: reading the fixture as if it were somebody's money.  The directory is
``mock_dataset/`` now, and it says what it is.

The old name is still resolved when the new one is absent, because the tests
build tiny throw-away fixtures under that name and a rename must not silently
turn a real read into an empty frame.
"""
from __future__ import annotations

from pathlib import Path

#: Directory names checked in order.  The first one that exists wins.
FIXTURE_DIR_NAMES = ("mock_dataset", "dataset")


def repo_root(root=None) -> Path:
    """The repository root, or ``root`` when a caller supplies one."""
    if root is not None:
        return Path(root)
    return Path(__file__).resolve().parents[1]


def fixture_dir(root=None) -> Path:
    """The calibration fixture directory for ``root``.

    Returns the path even when nothing is there, so a caller can report the
    location it looked in instead of raising from a loader.
    """
    base = repo_root(root)
    for name in FIXTURE_DIR_NAMES:
        candidate = base / name
        if candidate.is_dir():
            return candidate
    return base / FIXTURE_DIR_NAMES[0]


def fixture_file(name: str, root=None) -> Path:
    return fixture_dir(root) / name


def fixture_name(root=None) -> str:
    """The directory name actually in use, for labelling a screen."""
    return fixture_dir(root).name
