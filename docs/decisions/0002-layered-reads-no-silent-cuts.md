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
- past a per-entity budget on the children's bodies, the server drops the
  remaining bodies but keeps the tree.

Every cut is stated in the answer, with a count and the call that gets the
rest.

Open: Claude Code caps an MCP result at 25,000 tokens by default and warns
at 10,000, so a whole record can hit the host's cap. The server can declare a
per-tool limit with `_meta["anthropic/maxResultSizeChars"]`. Not decided yet.
Seen live: a 250-span trace read is about 38,000 tokens with bodies slimmed,
and the host rejects it, so the caller gets nothing (`/dogfood`, OPIK-8485).

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
