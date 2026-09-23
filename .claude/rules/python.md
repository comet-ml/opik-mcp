---
paths:
  - "**/*.py"
---

# Python

- uv only (`uv run`, `uv add`). Never pip or poetry.
- Full annotations, no `Any`. Fix a type error instead of adding
  `type: ignore`; the codebase has two.
- `from __future__ import annotations` at the top of each module. Imports at
  the top, never inside functions.
- Frozen dataclasses for internal contracts; Pydantic models at the edges
  (tool input, backend payloads).
- Keyword-only arguments (`*`) when positional arguments could be swapped.
- Names say exactly what the thing is: `experiments_compare_url`, not
  `compare_url`.
- Errors: raise a typed error, chain it with `from`, give it an `error_kind`
  class variable. Analytics groups errors by class, never by message text.
- No `print`, no `noqa`, no divider comments.
- No speculative abstractions, no just-in-case error handling, no
  back-compat shims. Don't reformat code you didn't change.

## Comments and docstrings

Write one only when the code can't say it: a non-obvious reason, a hidden
constraint, a workaround for a backend quirk. Never restate the name, the
signature or what the next lines do. No module docstring by default, no
`Args:`/`Returns:` sections. Existing comments stay; new code follows this.

Good:

```python
# The backend cuts each field at 10,001 characters, so one span can exceed
# the budget on its own.
```

Bad:

```python
# Loop over the spans and add each one to the result.
```
