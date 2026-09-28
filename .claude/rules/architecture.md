---
paths:
  - "src/**"
---

# Architecture

One entity, one namespace. A package root holds only protocols, base classes,
registries and generic mechanisms. Reasoning and the before/after example are
in [ADR 0004](../../docs/decisions/0004-entity-logic-in-its-namespace.md).

- A read entity is everything under `read_list/entities/<entity>`: its OQL
  fields, sort fields, URI pattern, UI links and layout. The root reaches them
  only through hooks on its `EntityHandler`.
- A write operation is everything under `writes/operations/`: its model, wire
  shape and refusal text. The root reaches them only through hooks on its
  registry entry.
- Adding one means a namespace plus one line in the registry. If the root has
  to change, a hook is missing. Add the hook and say so in the PR.
- Entities never import each other. A fact two entities need becomes a root
  mechanism that names neither.
- A root module names no entity or operation. `tests/repo/ratchets.json` holds the
  shrink-only list of today's exceptions. Touching a listed module means moving
  its entity part out and deleting the entry.
- A fact lives in one place: skill routing in `skills_catalog`, an entity's
  vocabulary in the entity. Instructions and descriptions point at it.
- Call flow is one direction: tool → dispatcher → handler or operation →
  `opik_client` → backend. Nothing below imports anything above.

A failing guard test names the file and where the code belongs. Follow it.

Good: the entity plugs its links in through a hook on its handler.

```python
# read_list/entities/thread.py
HANDLER = EntityHandler(entity_type="thread", link_fn=thread_links, ...)
```

Bad: a root module keeps a table keyed by entity name, so every new entity
edits the root and the entity's facts live in two places.

```python
# read_list/sorting.py
SORTABLE_FIELDS = {"trace": (...), "thread": (...)}
```
