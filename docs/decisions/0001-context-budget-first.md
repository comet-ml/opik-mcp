# 0001 — Context budget first

Status: accepted, 2026-09-23 (OPIK-8485)

## Decision

Every token the server puts into a host's context has to pay for itself. This
is the main rule, and other decisions follow from it.

- The advertised tool surface has a hard byte budget. It is loaded in every
  request the host makes.
- Every answer states its size, so the caller can decide to narrow.
- The caller can always narrow: a span instead of its trace, a filter instead
  of a page, a window instead of all time, `fields=[…]` instead of the record.
- A change that adds context says what it costs and what it adds, with
  measured bytes or tokens in the PR.

## Why

Context is the host's scarcest resource. A tool that answers with more than
was needed costs the user money and crowds out their own work.

## Enforced by

- `tests/conformance/test_tool_inventory.py`: surface budget (24,000 bytes)
  and its history comment.
- The size header on every read (`read_list/size.py`): its format in
  `tests/test_read_list/test_link_shape.py`, its presence on reads in
  `tests/test_read_list/test_read_tool.py`.

## Log

- 2026-09-11: surface budget raised to 24,000 bytes, about 3.5 KB above the
  measurement; it guards against accidental growth, not against wording (#187).
- 2026-09-22: token estimate set to 2.5 characters per token, because answers
  are JSON, not prose (#199).
