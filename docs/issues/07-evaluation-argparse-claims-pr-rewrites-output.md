---
title: evaluation.run argparse text claims PR mode rewrites output.csv
labels: bug,docs,p3
---

## Context

`evaluation/run.py` still describes PR mode as regenerating `output.csv`, while
`run_pr` uses `write_output=False` (confirmed by G4 reporting the unchanged
output hash).

## Acceptance

The argparse description and module docstring match the code; a test pins the
sentence.
