---
title: Deferred forecast corrections (MTD + proration) conflict with G1 safety
labels: known-issue,kernel,p1
---

## Context

The review flagged three forecast defects: partial-month history, month-to-date
double counting, and proration using a fixed 30-day month.

The partial-month exclusion shipped (G1-neutral). The other two were implemented,
measured, and **removed** before release: enabling them produced 2 status
upgrades and 15 safe overclaims on the 25 sample rows, turning the required G1
gate red.

## Evidence

- `docs/g2-hypothesis-report.md` — the three rules and their measured effects.
- `docs/release-verification.md` — G1 is 0/0/0 with the corrections removed.

## Why not a flag

An earlier version gated them behind `BUY_WAIT_FORECAST_CORRECTIONS`. An
independent review showed an env switch inside the decision path changes 186 of
250 rows, so `output.csv` bytes depended on the shell. The flag and the
corrections were removed.

## Proposal

Design a safety margin that keeps G1 at 0/0/0 while the corrected arithmetic is
used, then re-introduce it without a runtime switch.

## Acceptance

The corrected forecast runs by default; G1 stays 0/0/0; `output.csv` is
deterministic across environments.
