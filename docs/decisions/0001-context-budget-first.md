# 0001 — Context budget first

Status: accepted, 2026-09-23 (OPIK-8485)

## Decision

Every token the server puts into a host's context has to pay for itself. This
is the main rule, and other decisions follow from it.

- The advertised tool surface has a hard byte budget. It is loaded in every
  request the host makes.
- Every read states its size, so the caller can decide to narrow. List
  answers don't yet (backlog in `.claude/dogfood/memory/`).
- The caller can always narrow: a span instead of its trace, a filter instead
  of a page, a window instead of all time, `fields=[…]` instead of the record.
- A change that adds context says what it costs and what it adds, with
  measured bytes or tokens in the PR.

## Why

Context is the host's scarcest resource. A tool that answers with more than
was needed costs the user money and crowds out their own work.

## Enforced by

- `tests/conformance/test_tool_inventory.py`: the surface budget and its
  history comment, and the `initialize` instructions budget, which loads even
  when a host defers the tool list.
- The size header on every read (`read_list/size.py`): its format in
  `tests/read_list/test_link_shape.py`, its presence on reads in
  `tests/read_list/test_read_tool.py`.
- `tests/conformance/test_tool_annotations.py`: every tool carries a title
  and its hints, and each description and the instructions arrive whole
  under the host's cut.

## Log

- 2026-09-11: surface budget raised above the measurement; it guards against
  accidental growth, not against wording (#187). The ceiling and its history:
  `SURFACE_BUDGET_BYTES` in `tests/conformance/test_tool_inventory.py`.
- 2026-09-22: token estimate tuned for JSON answers, not prose (#199). The
  ratio is `_CHARS_PER_TOKEN` in `src/opik_mcp/read_list/size.py`.
- 2026-09-23: Claude Code cuts tool descriptions and the instructions at a
  fixed length, checked live: `write` loses its last three operations and the
  name of a fourth, and `read`, `read_skill` and the instructions lose their
  tails. The limit is `DESCRIPTION_LIMIT` in
  `tests/conformance/test_tool_annotations.py`, which pins the long ones as
  strict expected failures until they are rewritten (OPIK-8485 follow-up).
  Titles and hints added to all tools; their bytes are in the history comment
  in `tests/conformance/test_tool_inventory.py`.
- 2026-09-23: instructions budget added. With tool search on, they are the
  part every session still loads; the measurement is in the comment on
  `INSTRUCTIONS_BUDGET_BYTES` in `tests/conformance/test_tool_inventory.py`
  (OPIK-8485).
- 2026-09-25: correction: the 2026-09-11 line said the ceiling sat about
  3.5 KB above the surface. Measured after OPIK-8485, the headroom was about
  505 bytes. Numbers the tests own were removed from this ADR (OPIK-8496).
