---
title: The JSON/TUI account save path is not gated by the validator
labels: known-issue,accounts,p2
---

## Context

`code.accounts.save` now runs `code.account.validate_account` (commit `a896e04`).
`code.account.save_account` (the JSON/TUI path) does not.

## Acceptance

Every persistence entry point calls the same validator; a test pins both paths.
