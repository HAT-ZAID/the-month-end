---
title: Missing-fact vocabulary is duplicated between service and solver
labels: tech-debt,p3
---

## Context

`code/service.py` defines `MISSING_FACTS` and `code/solver.py` defines
`POSITION_REQUIRED_FACTS` for the same two field/label pairs. They are kept in
step only by comments.

## Acceptance

One shared definition; both modules import it; a test pins the labels.
