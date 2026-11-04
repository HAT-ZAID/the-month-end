---
title: ChatOpenAI retries can outlive the per-attempt timeout
labels: known-issue,agent,p2
---

## Context

The transport deadline is passed per attempt. The SDK's default retry policy can
start another attempt after the outer deadline, so the recorded
`transport_timeout_s` is a per-attempt bound, not a wall-clock one.

## Proposal

Set `max_retries=0` on the provider client and let the orchestrator decide
retries, so the deadline is authoritative.

## Acceptance

One model call per attempt; no hidden retry after the deadline; the trace matches
observed wall clock.
