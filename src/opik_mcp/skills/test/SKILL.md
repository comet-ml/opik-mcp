---
name: test
description: Turn a failing case into a repeatable check — create a dataset item, a test suite, and a scoring assertion so a fix can be verified by /compare.
last_updated: "2026-07-30"
source_commit: "TODO"
---

# Test — capture a failing case as a repeatable check   <!-- SCAFFOLD (OPIK-7650) -->

> **Scaffold — not yet implemented.** Behavior is specified below; the steps are TODO.

## Intent
Capture a trace as a regression case: extract input/expected, create or append a dataset + test suite with a scoring assertion, and name it so `/compare` can run against it.

## Transport
Creates datasets/suites via the SDK (`create_dataset`, `create_test_suite`, `TestSuite.insert`) or the hosted MCP `write` tool (recommended). Does not require the MCP.

## Definition of done
- [ ] From a trace, one invocation produces a runnable test-suite case; works over the SDK, uses the MCP when present.
