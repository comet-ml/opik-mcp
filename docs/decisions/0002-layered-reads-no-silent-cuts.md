# 0002 — Layered reads, no silent cuts

Status: accepted, 2026-09-23 (OPIK-8485)

## Decision

The caller chooses how much comes back, in layers:

1. `list` returns short rows.
2. `fields=[…]` returns only the named paths, from a list or a read.
3. `read` returns the whole record.

The record you ask for always comes back whole. A composite read (a trace, a
thread) inlines its children in a slimmer form, and every child carries the id
that fetches it whole:

- the backend cuts long fields on children (`truncate=true`);
- past a per-entity budget (14,000 characters of span bodies on a trace), the
  server drops the remaining children's bodies but keeps the tree.

Every cut is stated in the answer, with a count and the call that gets the
rest.

## Why

A silently short answer costs a conclusion: the user blames the MCP, not the
truncation. A large answer costs context, which the caller can see and narrow
(0001).

## Enforced by

- `tests/test_read_list/test_read_tool.py`: `test_a_huge_record_comes_back_whole`,
  `test_a_traces_spans_are_asked_for_slim`,
  `test_the_dropped_bodies_are_counted_and_one_call_away`.
- `tests/test_read_list/test_fields.py`: projection names what it keeps.
- `tests/test_read_list/test_dataset_items.py`: every cut is declared.

## Log

- 2026-09-11: server-side compression tiers (full, medium, skeleton chosen by
  `max_tokens`) removed. They cut answers the caller could not get back (#187).
- 2026-09-21: `fields=[…]` added as the middle layer (#197).
- 2026-09-22: inline budget for children's bodies, declared in the answer (#199).
