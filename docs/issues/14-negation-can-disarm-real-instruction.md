---
title: An unrelated negation can disarm a real instruction
labels: known-issue,intent,p3
---

## Context

CR-18 made negation fail-safe: it is checked before the first stop/reduce verb,
so `"I am not sure, stop my subscription"` refuses rather than mutates. That
prevents accidental mutation but also disarms a genuine instruction.

## Proposal

Bind the negation to the action clause it modifies (e.g. "do not stop X") rather
than to the whole utterance.

## Acceptance

`"I am not sure, stop my subscription"` asks for clarification or applies;
`"do not stop my subscription"` never mutates.
