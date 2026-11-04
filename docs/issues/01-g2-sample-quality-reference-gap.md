---
title: G2 sample-quality gate fails against the organiser's reference arithmetic
labels: known-issue,evaluation,p1
---

## Context

`python -m evaluation.run --mode pr` reports G2 as FAIL: 3/25 full rows, 3/25
`amount_safe_to_pay`, 17/25 status, 17/25 method, 16/25 plan, 16/25 earliest.

G0, G1, G3 and G4 pass. G2 is the only failing gate.

## Evidence

`docs/g2-hypothesis-report.md` records roughly 250 scored forecast variants across
structure, estimators, history windows, horizons, proration, month-to-date scope,
income cadence, event-status filters and expense scaling — plus a fifteen-model
zoo, SHAP/partial-dependence inspection of an overfitted ensemble, and a
1,000-shuffle permutation test. None reproduced a single row where
`0 < gold < requested`.

The sample file arrived with the challenge dataset before any code in this
repository existed, so the gold is the organiser's reference output, not an
earlier version of this kernel.

## What would close it

1. The organiser's reference implementation or generator, **or**
2. additional labelled rows, **or**
3. an explicit, documented re-baseline of the G2 thresholds.

Fitting the 25 gold rows is rejected: a model that memorises the sample says
nothing about the 250 real requests.

## Acceptance

G2 either passes against a supplied reference, or its threshold is re-baselined
with a recorded product decision.
