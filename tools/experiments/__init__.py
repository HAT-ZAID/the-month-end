"""Experiments and audits: measurement only, never a decision source.

Nothing in this package may be imported by the decision pipeline. Each module
reads the real kernel and reports on it, so a result here can justify a change
but can never make one. Promoted behaviour belongs in ``code/`` with tests.

Run them as modules from the repo root, e.g.::

    python -m tools.experiments.compare_jev --dry-run
"""
