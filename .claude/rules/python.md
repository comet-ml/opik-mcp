---
paths:
  - "**/*.py"
---

# Python

`make check` enforces the mechanical rules: ruff and mypy in strict mode, with
the configuration in `pyproject.toml`. Fix the finding, never the rule. No
`noqa`, no `type: ignore`, no loosening of the config in a feature PR. A rule
that is wrong changes in its own PR, with the reason.

What the tools can't check:

- A name says what the thing is, in full. `experiments_compare_url`, not
  `compare_url`. `dropped_span_bodies`, not `n`. Boolean names read as a
  question: `is_local`, `has_more`.
- Keyword-only (`*`) when two parameters share a type. A call site should read
  without the signature open.
- Frozen dataclasses for values that cross a module boundary. Pydantic only
  where data enters or leaves the process: tool input, backend payloads.
  Exception classes are plain classes.
- One typed error per failure mode, with an `error_kind` class variable, raised
  with `from`. Analytics groups by class, so a new way to fail is a new class,
  never a new message string.
- A function that needs a comment to say what it does needs a better name or a
  split.
- Delete what the change makes unused. No compat shim, no just-in-case `try`,
  no abstraction with one caller.
- Don't reformat lines you didn't change.
- `.claude/hooks/` and `scripts/dev/` use the standard library only and run
  with `python3`, so they work before `uv sync`. Printing is their output.

## Comments and docstrings

A comment says what the code can't: a reason, a hidden constraint, a backend
quirk. Never what the next lines do.

Good:

```python
# The backend cuts each field at 10,001 characters, so one span can exceed
# the budget on its own.
```

Bad:

```python
# Loop over the spans and add each one to the result.
```

A docstring is one sentence on the part of the contract the signature doesn't
show. No `Args:` or `Returns:` sections. A module gets a docstring only when
its name doesn't say what it is for.
