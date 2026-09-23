# 0004 — Entity logic lives in its namespace

Status: accepted, 2026-09-23 (OPIK-8485)

## Decision

All logic for one entity or write operation lives in that entity's or
operation's namespace. A package root holds only protocols, base classes,
registries and generic mechanisms. That includes tables: per-entity OQL
fields, sort fields, URI patterns and link builders belong with the entity,
reached through a hook on its handler.

If a change for one entity has to touch the root, the framework is missing a
hook. Add the hook, and say so in the PR.

## Why

A root module that knows every entity changes in every PR, however narrow the
PR is. A root that only dispatches rarely changes, so it is easy to extend and
to review. Raised in review of #186 and #187.

## Enforced by

- `tests/test_read_list/test_modular.py`: no entity module at the root, no
  entity imports another, the registry is only a table, dispatchers name no
  entity, and no new entity name at the root.
- `tests/test_writes/test_dispatch_stays_generic.py`: the dispatcher names no
  operation, hooks come from `writes/operations/`, and no new operation name
  at the root.
- Today's root modules that still name entities are listed in those tests. The
  lists may only shrink. OPIK-8496 works through them.

## Log

- 2026-09-10: write-operation logic moved behind registry hooks (#186).
- 2026-09-11: read entities moved under `entities/`, one namespace each (#187).
- 2026-09-23: the rule extended to tables, with a shrink-only allowlist (OPIK-8485).
