---
title: decision_explanation never names irregular_income
labels: known-issue,ux,p2
---

## Context

Income recorded without `recurrence="monthly"` is stored as `irregular_income`
and is deliberately not projected. The explanation is generated generically in
`code/solver.py` / `code/explanations.py`, so the user is never told that this
income will not affect the forecast.

## Acceptance

A decision containing an `irregular_income` row says so in
`decision_explanation`, and a test pins the sentence.
