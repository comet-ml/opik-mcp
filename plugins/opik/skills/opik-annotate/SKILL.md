---
name: opik-annotate
description: Set up human review in Opik — create an annotation queue for a project's traces or threads and give it a custom annotator view, an HTML page that shows reviewers each item the way they need to judge it (the email beside the reply, the conversation as chat bubbles) instead of raw JSON. Writes through the Opik MCP when connected, the REST API otherwise. Returns the queue, its link, and what reviewers will see. Use for "build a custom annotator view", "set up an annotation queue", "make the review queue show the email and the reply side by side", "change what annotators see", "remove the custom view". Not for automatic scoring (use online-eval) or for finding which traces to review (use diagnose).
compatibility: Tested with Claude Code; works with any Agent Skills-compatible host (Cursor, VS Code Copilot, Codex). Requires Opik configured and a project with traces. Custom annotator views need an Opik version that supports them; an older Opik accepts the view and drops it, and the MCP says so.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
metadata:
  last_updated: "2026-10-26"
  source_commit: "e4a6fa9b424128eb01817b8700761ac249a89e78"
  argument-hint: "[queue name or id, or what reviewers should judge; optional project]"
---

# Annotate — A Review Queue Reviewers Can Read

**Definition of done:** the **queue exists with the view saved, and the user knows where to check it** — the write returned without a `warning`, and the answer carries the queue's link. A view is built from the project's **real** traces or threads, never from a guessed shape. If the queue or the view can't be saved, stop at the **first** genuine blocker and return **exactly one** next step.

Operate: **look at real items first, cover every shape you saw, show text not JSON, keep scoring in Opik's own panel.** This skill writes to Opik only and changes no application code.

## Inputs

`/opik-annotate <what reviewers judge>` ("the drafted reply", "whether the agent solved the ticket"), `/opik-annotate <queue name or id>` (change that queue's view), or a request to remove a view. Infer the rest; optional overrides: project (default: configured) · scope (default: `trace`; `thread` for whole conversations) · feedback definitions (default: the queue's own).

## Activation — the only in-scope work

### 1. Resolve the project and the queue
With the Opik MCP connected: `list('annotation_queue', project_name=…)` shows the project's queues with their scope and item count; `read('annotation_queue', id_or_name)` returns one with its instructions, feedback definitions and view summary. The view's html is stated by size; `read('annotation_queue', id, fields=['annotator_view.html'])` returns it whole — read it before changing a view, and change only what was asked.

### 2. Look at real items before writing anything
- Queue with items: `list('trace', project_name=…, filters='annotation_queue_ids contains "<queue_id>"', size=20)` (`list('thread', …)` for a thread queue), then `read('trace', id)` on two or three that differ.
- Empty or new queue: the project's latest traces or threads instead, and say so ("the queue has no items yet, so the view is built from the latest traces").

Note every shape `input`, `output` and `metadata` take: objects, arrays, or strings that hold JSON. A view that handles only the most common shape fails reviewers on the rest.

### 3. Decide what reviewers need
From the queue's instructions and feedback definitions: what is judged goes first; what it is judged against goes beside or below it; what would bias the reviewer (judge scores, when they give their own verdict) goes behind `kit.revealGate`.

### 4. Write the view
Read `references/kit-v1.md` first: it is the only API, class and token list the page has, and views are stored with that kit version. What the page can rely on:
- Rendered in a sandboxed frame: **no network** (no fetch, external script, stylesheet, font or image), no forms, popups or navigation. Inline `<script>` and `<style>` only.
- Build elements with `kit.h` and `text`; `innerHTML` and its relatives are blocked.
- Render through `kit.mount(render)` exactly once. `render` gets `{ trace }` for a trace queue and `{ thread, traces }` (turns in order) for a thread queue; if `render` throws or returns nothing, reviewers get Opik's default view and see the error, so handle every shape you found instead of relying on that fallback.
- Trace fields: `id`, `name`, `input`, `output`, `metadata`, `tags`, `feedback_scores`, `start_time`, `end_time`, `duration`, `usage`, `total_estimated_cost`. Thread fields: `id`, `start_time`, `end_time`, `duration`, `number_of_messages`, `status`, `tags`, `feedback_scores`, `usage`, `total_estimated_cost`. No spans; attachments stored outside the trace are not available, media embedded as `data:` URLs is.
- `feedback_scores[]` with `source === "ui"` are human; the rest are judges.
- Style with `var(--kit-…)` tokens, never hard-coded colours: light and dark themes share the view. Layout must work from 360px wide.
- At most 200,000 characters.

Design: lead with what is judged in a `highlight` card; a row of `kit.chip` facts that frame the item; counts that matter (words against a limit); fold long text with `kit.clampGroup` and lists over ~5 items; nothing scrolls inside the view; technical details (model, ids, durations) in a collapsed `<details>` at the bottom. Normalise every shape into one before rendering. For a thread, `kit.turn` per message, user on the right, tool calls in a collapsed `<details>`.

### 5. Save it
MCP: `write('annotation_queue.create', {project_name, name, scope, instructions?, feedback_definition_names?, annotator_view: {html, summary}})`, or `write('annotation_queue.update', {id, annotator_view: {html, summary}})` for an existing queue. `summary` is one or two sentences starting "Annotators see…" describing the whole view; rewrite it on every change. Remove a view with `write('annotation_queue.update', {id, clear_annotator_view: true})`. `schema('annotation_queue.update')` has the full input.

Without the MCP: `PATCH /v1/private/annotation-queues/{id}` (or `POST /v1/private/annotation-queues`) with the same body as JSON, `annotator_view: {}` to clear, `kit_version: 1` beside `html`. Reading it back is then yours to do: `GET /v1/private/annotation-queues/{id}` returns `annotator_view` when it was stored.

### 6. Report
The queue link, one sentence on what reviewers now see, and where to check it: the queue's **Edit → Annotator view → Preview** shows the view on sample items, and `annotator_url` opens the page reviewers work the queue on. Offer two or three next edits that fit the data ("fold the tool calls", "hide the judge scores until revealed").

## Blockers

Stop at the **earliest** and return **exactly one** next step:
- "Connect the Opik MCP or run `opik configure`, then rerun `/opik-annotate`."
- "Which project's traces should reviewers see? Pass `/opik-annotate <what> <project>`."
- "The project has no traces yet. Send a few through the app, then rerun." A view written without data is a guess.
- The write's `warning` says the backend did not store the view → "This Opik version doesn't support custom annotator views; upgrade Opik, then rerun." The queue itself was saved.

## Output

**User-facing:** a short message — the queue as a link, what reviewers see in one or two sentences, where to preview it, the next edits. Not JSON, not the HTML.

**Underneath**, one shape:
- `status`: `saved` | `cleared` | `view_dropped` | `blocked`
- `queue`: `id`, `name`, `scope`, `url`, `annotator_url`
- `view`: `summary`, `html_chars`, `shapes_covered` (how many input/output shapes the view handles, of how many seen)
- `sampled_from`: `queue_items` | `project_latest`
- `source`: `mcp` | `rest`
- `next_step`: exactly one

Invariants: `saved` means the write returned no `warning`; every view was built after reading real items; `blocked` and `view_dropped` carry exactly one `next_step`; every path leaves the codebase unchanged.

## Example (thread queue)

```html
<script>
const { h, first, get, asText } = kit;
const say = (value, keys) => asText(first(...keys.map((key) => get(value, [key])), value));

kit.mount(({ thread, traces }) => {
  if (!traces.length) return undefined;
  const fold = kit.clampGroup();
  return h("div", { class: "kit-stack" },
    h("div", { class: "kit-row" }, kit.chip("Turns", traces.length, "primary"), kit.chip("Status", thread.status)),
    traces.flatMap((trace) => [
      kit.turn({ role: "user", title: "Customer", body: fold.text(say(trace.input, ["message", "question", "input"])) }),
      kit.turn({ role: "assistant", title: "Agent", body: fold.text(say(trace.output, ["answer", "output", "response"])) }),
    ]),
  );
});
</script>
```

## Anti-patterns
A view written before reading any item; raw `JSON.stringify` of `input`/`output` for reviewers; scoring widgets in the view (scoring stays in Opik's panel); helpers or classes not in `references/kit-v1.md`; network calls or external assets; `innerHTML`; hard-coded colours; judge scores shown next to the reviewer's own verdict; rewriting a whole view to change one part; declaring success when the write carried a `warning`; **editing application code**.

## References

`references/kit-v1.md` — the view kit v1: rules, data passed per queue type, tokens, classes and helpers, with an example. Trace, thread and feedback-score concepts: `../opik/references/observability.md` in the `opik` skill. If that skill isn't installed, use <https://www.comet.com/docs/opik/> rather than working from memory.
