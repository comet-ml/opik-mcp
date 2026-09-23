---
paths:
  - "tests/**"
---

# Tests

- Test the property, not its presence. "The link is there" proves little;
  "the link opens the page for this record" is the test.
- Go through the registry. A test that calls a function nothing registers
  only proves the logic runs.
- Prefer real e2e data to large stubs. A stub that grows past a screen is
  testing itself.
- A guard test also checks it is doing work, e.g. that the default it guards
  against is still on.
- Markers `e2e`, `live` and `user_flows` are deselected by default and run by
  `make e2e`, `make live` and `make user-flows`.
- Telemetry is off for the test process (`tests/conftest.py`). Never turn it
  on outside a test that asserts on it.
- A new module-level cache or flag needs a reset in the autouse fixture in
  `tests/conftest.py`.
