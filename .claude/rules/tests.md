---
paths:
  - "tests/**"
---

# Tests

- The name is the property: `test_the_dropped_bodies_are_counted_and_one_call_away`,
  not test_read_trace. A test that still passes with the change reverted is
  not a test of the change.
- Go in through the door a caller uses: the MCP session for a tool, the
  registry for an entity, the dispatcher for a write. Call a helper directly
  only to test the helper.
- A stub shapes only what the test reads. A stub longer than a screen is a
  fixture file. A fixture longer than two screens belongs in the e2e or live
  suite with real data.
- A guard test proves it is guarding: it asserts the default it protects is
  still on, and its failure message names the file, where the code belongs and
  the rule.
- A ratchet only shrinks. Paying off an item means deleting it from the list,
  and the test fails if you don't.
- Slow suites carry their marker and run only through their make target.
  Never mark a fast test.
- Telemetry is off for the whole test process. Assert on it only in the test
  that turns it on.
- New module-level state needs a reset in the autouse fixture in
  `tests/conftest.py`, or tests pass by run order.
- Failure output is for an agent: one message that says what differed. Long
  data goes to a file the message names.
- Test behaviour, not wording. Assert the tool the host picks or the shape of
  the answer, not the text of a description or prompt.

Good:

```python
async def test_a_huge_record_comes_back_whole() -> None:
```

Bad:

```python
def test_read() -> None:
    assert "trace" in result
```
