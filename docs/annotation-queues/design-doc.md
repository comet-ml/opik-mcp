# annotation-queues

## Purpose

This feature lets an agent work with a project's annotation queues and the
custom annotator view a queue can carry: list and read queues, create one,
change one, and set, replace or remove its view. It answers what a read of a
queue returns when its view is large, how a view is cleared, and what happens
on an Opik that predates custom views.

## What it does now

### Listing and reading

`list('annotation_queue', project_id=… | project_name=…)` returns one page of
the project's queues. Project scope is required, as on the queue pages of the
Opik UI.

- The backend's list endpoint has no project parameter, so the project travels
  as a `project_id =` clause added to the caller's `filters`.
- `filters` and `sort` take the backend's queue fields: name, description,
  instructions, `scope` (`trace` or `thread`), `feedback_definition_names`,
  created and updated time and user. `name` is a substring match.
- List rows never carry the view; the backend selects it only by id.

`read('annotation_queue', id_or_name)` returns the queue with a link to its
page under the project.

- A plain read does not inline `annotator_view.html`. `annotator_view` carries
  `summary`, `kit_version` and `html_chars`, and `annotatorViewHtml` states
  the size and the call that returns it:
  `read('annotation_queue', '<id>', fields=['annotator_view.html'])`.
- A queue with no view says so in `annotatorViewHtml`, and
  `fields=['annotator_view.html']` answers `null` for it.
- `fields=[…]` sees the whole record, html included.

`list('trace' | 'thread', filters='annotation_queue_ids contains "<id>"')`
lists a queue's items. That filter names a parent, so the `source = "sdk"`
default is not added: a queue also holds playground and experiment traces.

### Writing

- `annotation_queue.create`: `name`, `scope`, project (`project_id` or
  `project_name`), optional `id`, instructions, feedback definitions,
  annotators per item, lock timeout and `annotator_view`.
- `annotation_queue.update`: `id` plus any of the editable fields. Only the
  fields passed change. `clear_annotator_view: true` removes the view.
- `annotator_view` is `{html, kit_version, summary}`: html up to 200,000
  characters, `kit_version` 1 (the default), summary up to 1,000. The limits
  are checked before anything is sent.
- The answer links the queue's page. A create without an `id` links the
  project's queues, since the backend chose the id.
- A write that carried html reads the queue back. When the view is not there,
  the answer is still `ok` and adds a `warning` that this Opik did not store
  it. When the read-back fails, the `warning` says the view could not be
  confirmed.

There is no delete, for queues or their items, and no way to add items yet.

### Authoring a view

The `opik-annotate` skill holds the workflow: read the queue, sample real
items, write the view, save it, check it in the queue's Edit dialog under
Annotator view, Preview. Its `references/kit-v1.md` is a verbatim copy of the
kit reference in opik's frontend, the only API a view can use.

## How it works

```
read / list  -> read_list/entities/annotation_queue.py  -> client/annotation_queue.py
write        -> writes/registry.py ("annotation_queue.*")
             -> writes/operations/annotation_queue.py (prepare, build, decorate, confirm_view)
             -> models in writes/operations/observability.py
```

- What a plain read shows: `plain_read_view`, wired as the entity's
  `read_view_fn` (`read_list/handler.py`), which `read_tool.py` applies only
  when `fields` is not given.
- The project for a link: `prepare` resolves it before the write, from the
  caller's project on a create and from the queue itself on an update. A
  missing queue is refused there; any other failure only costs the link.
- Clearing: `build` sends `{"annotator_view": {}}`. `wire.dump` drops `None`,
  and the backend reads an absent view as "unchanged".
- The read-back: `confirm_view`, wired as the operation's `confirm_fn`, which
  the dispatcher runs after a write the backend accepted. It takes the queue id
  from the request, or from the response's `Location` for a create without one.

## Decisions

- A plain read states the html by size instead of returning it. Most
  questions about a queue need none of it, and a view near the backend's cap is
  more than Claude Code accepts in one answer. Logged in
  [ADR 0002](../decisions/0002-layered-reads-no-silent-cuts.md) (OPIK-8951).
- The read-back after a write: every released Opik before custom views ignores
  `annotator_view` and answers 2xx, so without it an agent reports a dropped
  view as saved. It runs only when html was sent (OPIK-8951).
- Write scope is create and update. Adding items is a separate change; delete
  stays out, as for every other operation ([writes](../writes/design-doc.md)).
- The models sit in `observability.py`, which is on the `Any` baseline; the
  hooks module is typed strictly. See the writes doc for why.
- The kit reference is a copy pinned to the `kit_version` the writes send,
  re-copied by hand when Opik changes the kit (`test_annotate_kit.py`).

## Proven by

- Reads, the html notice, the project clause, the link:
  `tests/read_list/test_annotation_queue.py`,
  `test_a_read_view_shapes_a_read_without_fields_only`.
- The queue filter keeps every source:
  `test_a_filter_on_an_annotation_queue_gets_no_sdk_default_on_top`.
- Writes, clearing, the read-back, refusals:
  `tests/writes/test_annotation_queue.py`, and over stdio and HTTP
  `tests/hermetic/writes/test_annotation_queue.py`.
- The description's claims: `tests/hermetic/test_description_claims.py`.
- The kit copy's version: `tests/skills/test_annotate_kit.py`.
- Against a real Opik:
  `test_an_annotation_queue_view_is_set_read_back_by_size_and_cleared`.

## Log

- 2026-10-23: annotation queues readable and writable, with custom annotator views and the `opik-annotate` skill (OPIK-8951).
