---
title: Provider calls cannot be killed; the deadline is per attempt
labels: known-issue,agent,p2
---

## Context

CR-10 lowered the transport deadline and records `cancellation_confirmed`, but a
daemon thread cannot be killed. A timed-out call may still finish in the
background, and `ChatOpenAI` may retry under its own policy.

`docs/release-verification.md` records this as a known limitation rather than
claiming cancellation.

## Proposal

Run provider calls in a killable process (or set `max_retries=0` and use a
cancellable transport), and record whether the os-level process was terminated.

## Acceptance

No inference continues after a timeout; the trace proves it; retries cannot
outlive the deadline.
