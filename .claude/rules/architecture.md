---
paths:
  - "src/**"
---

# Architecture: entity logic lives in its namespace

See [ADR 0004](../../docs/decisions/0004-entity-logic-in-its-namespace.md).

- All logic for one read entity lives under `read_list/entities/<entity>/`;
  all logic for one write operation under `writes/operations/`.
- A package root holds only protocols, base classes, registries and generic
  mechanisms. Tables count: per-entity OQL fields, sort fields, URI patterns
  and link builders belong with the entity, reached through a hook on its
  handler.
- Adding an entity means a namespace plus one import and registration in the
  registry. Nothing else at the root should change.
- If a change for one entity has to touch the root, a hook is missing. Add
  the hook and say so in the PR.
- Entities never import each other. A fact two entities need moves to the
  root as a shared mechanism.
- Data lives in its catalog (skills in `skills_catalog`), not repeated in
  instructions or descriptions.

The guard tests list today's root modules that still name entities. That list
is debt: shrink it when you touch that code. Some logic has no hook yet
(filter and sort fields, schema notes, URI patterns, write models). A new
entity that needs it adds its name to the list and says why in the PR; that
is expected until OPIK-8496 adds the hooks.

Before (today): the thread's URL shape sits in the root module and the
entity imports it back.

```python
# read_list/ui_links.py
def thread_page_url(settings: Settings, project_id: str, thread_id: str) -> str | None: ...

# read_list/entities/thread.py
from opik_mcp.read_list.ui_links import thread_page_url
```

After: the root keeps the generic part (`opik_ui_base`, `project_page_url`);
the thread builds its own URL and hands it over through `link_fn`.

```python
# read_list/entities/thread.py
def _thread_url(settings: Settings, project_id: str, thread_id: str) -> str | None:
    if not thread_id:
        return None
    return project_page_url(
        settings, project_id, "logs", query=f"logsType=threads&thread={thread_id}"
    )

HANDLER = EntityHandler(entity_type="thread", link_fn=thread_links, ...)
```
