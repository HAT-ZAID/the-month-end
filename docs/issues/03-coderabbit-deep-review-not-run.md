---
title: CodeRabbit deep review was not run on the remediation range
labels: process,p2
---

## Context

`AGENTS.md` §5 requires a clean CodeRabbit deep review before push. The CLI is
installed but reports an unauthenticated session:

```
coderabbit review --deep --committed --base-commit b757162 --agent
Review failed: Your CodeRabbit session or Git provider access could not be
authenticated. Run `coderabbit auth login`, then retry the review.
```

The four findings it had stored from a previous run were fixed (`6e59a5b`,
`9ac4384`, `5b72f18`, `ba358d0`), and the two independent reviews ran and had
their findings fixed.

## Acceptance

`coderabbit auth login`, then a clean deep review over `b757162..HEAD`.
