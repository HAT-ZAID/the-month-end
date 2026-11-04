---
title: Tool outputs are truncated at 4000 characters, which can break JSON
labels: known-issue,protocol,p2
---

## Context

`code/chat.py` serialises a tool result and cuts it at 4000 characters. A cut can
land inside a value, leaving the next model turn with ambiguous data.

## Proposal

Return valid envelopes with `has_more`, `next_cursor`, `returned_count` and
`total_count`, and filter before encoding.

## Acceptance

No tool response is invalid JSON; large results paginate; a test covers an
oversized result.
