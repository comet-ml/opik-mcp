"""The MCP App document: Opik's review UI, self-contained.

Everything is inline — MCP App resources render under a deny-by-default CSP, so no
CDN scripts, no Google Fonts, no external stylesheets, no remote images (the logo is
a data URI from ``assets.py``). The host↔app channel is the ext-apps postMessage
dialect (``ui/initialize`` → ``ui/notifications/initialized``, then
``ui/notifications/tool-result`` in, ``tools/call`` out), hand-rolled here so the app
carries no npm dependency.

Design tokens and layout come from opik-frontend: ``src/main.scss`` for the
light/dark variables and ``v2/pages/SMEFlowPage/AnnotationView`` +
``TraceMessages/TraceMessage.tsx`` for the transcript shape, so this reads as the SME
annotation view it stands in for. The card is deliberately a self-contained island
with the Opik wordmark and brand gradient: inside a chat, it has to be obvious whose
UI this is.
"""

from __future__ import annotations

from opik_mcp.apps.assets import LOGO_DARK, LOGO_LIGHT

REVIEW_HTML = r"""
<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8" />
<title>Opik — Thread review</title>
<style>
  /* --- tokens: opik-frontend/src/main.scss ------------------------------- */
  :root {
    /* The single source of truth for the panel's size — see .card. */
    --card-height: 620px;
    --background: 0 0% 100%;
    --soft-background: 240 20% 99%;
    --foreground: 224 17% 26%;
    --foreground-secondary: 224 71% 4%;
    --muted-slate: 215 16% 47%;
    --light-slate: 215 20% 65%;
    --border: 214 32% 92%;
    --primary: 239 89% 64%;
    --primary-hover: 238 62% 51%;
    --success: 142 64% 32%;
    --destructive: 348 86% 61%;
    --muted-disabled: 220 14% 96%;
    --message-input-background: #f2f2ff;
    --thread-icon-background: #3438d0;
    --thread-icon-text: #ffffff;
    --thread-active: #ebf2f5;
    --thread-inactive: #e2effd;
    --warning-soft: #fff7ed;
    --warning-line: #fdba74;
    --skeleton: 220 14% 94%;
    --skeleton-shine: 0 0% 100%;
    --card-shadow: 0 1px 2px rgba(16, 24, 40, .04), 0 4px 16px rgba(16, 24, 40, .06);
    --logo: url("__LOGO_LIGHT__");
  }
  html[data-theme="dark"] {
    --background: 0 0% 7%;
    --soft-background: 0 0% 9%;
    --foreground: 214 32% 91%;
    --foreground-secondary: 214 32% 91%;
    --muted-slate: 0 0% 64%;
    --light-slate: 0 0% 64%;
    --border: 0 0% 16%;
    --primary: 223 91% 60%;
    --primary-hover: 217 91% 70%;
    --success: 142 64% 40%;
    --destructive: 348 86% 61%;
    --muted-disabled: 0 0% 14%;
    --message-input-background: #2a2f47;
    --thread-icon-background: #2e2932;
    --thread-icon-text: #a06dd9;
    --thread-active: #24282b;
    --thread-inactive: #1e2a38;
    --warning-soft: #2a2016;
    --warning-line: #7c4a15;
    --skeleton: 0 0% 14%;
    --skeleton-shine: 0 0% 22%;
    --card-shadow: 0 1px 2px rgba(0, 0, 0, .3), 0 4px 16px rgba(0, 0, 0, .25);
    --logo: url("__LOGO_DARK__");
  }

  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: transparent; }
  body {
    font-family: Inter, system-ui, -apple-system, "Segoe UI", sans-serif;
    font-size: 14px; line-height: 1.5;
    color: hsl(var(--foreground));
    -webkit-font-smoothing: antialiased;
    padding: 2px;
  }
  .t-xs { font-size: 12px; }
  .t-xs-acc { font-size: 12px; font-weight: 500; }
  .t-title { font-size: 13px; font-weight: 600; color: hsl(var(--foreground-secondary)); }
  .mono { font-family: "Ubuntu Mono", ui-monospace, SFMono-Regular, Menlo, monospace; }
  .muted { color: hsl(var(--muted-slate)); }

  /* --- the island -------------------------------------------------------- */
  .card {
    display: flex; flex-direction: column;
    border: 1px solid hsl(var(--border)); border-radius: 14px; overflow: hidden;
    background: hsl(var(--background)); box-shadow: var(--card-shadow);
    /* Fixed height, deliberately. The host sizes the iframe from our size-changed
       report, so measuring our own content and reporting it is a feedback loop: any
       content taller than the number we sent makes the iframe itself scroll, and the
       reviewer ends up with two nested scrollbars. One constant, one scroller. */
    height: var(--card-height);
  }
  /* Opik brand gradient — the one unmistakable "this is ours" cue. */
  .accent { height: 3px; flex: 0 0 auto; background: linear-gradient(90deg, #FB9341, #E30D3E); }

  .brand {
    display: flex; align-items: center; gap: 10px; flex: 0 0 auto;
    padding: 10px 14px 8px; background: hsl(var(--soft-background));
  }
  .logo {
    width: 72px; height: 22px; flex: 0 0 auto; cursor: default;
    background: var(--logo) no-repeat left center / contain;
  }
  .brand-sep { width: 1px; height: 16px; background: hsl(var(--border)); }
  .brand-right { margin-left: auto; display: flex; align-items: center; gap: 10px; }

  .meta {
    display: flex; align-items: center; gap: 8px; flex: 0 0 auto; min-width: 0;
    padding: 0 14px 10px; background: hsl(var(--soft-background));
    border-bottom: 1px solid hsl(var(--border));
  }
  .thread-icon {
    width: 18px; height: 18px; border-radius: 5px; flex: 0 0 auto;
    display: flex; align-items: center; justify-content: center;
    background: var(--thread-icon-background); color: var(--thread-icon-text);
  }
  .id-chip {
    font-size: 12px; padding: 2px 7px; border-radius: 5px;
    background: hsl(var(--muted-disabled)); color: hsl(var(--muted-slate));
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 260px;
  }
  .status {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 11px; font-weight: 500; padding: 2px 9px; border-radius: 999px;
  }
  .status::before { content: ""; width: 5px; height: 5px; border-radius: 999px; background: currentColor; }
  .status.active { background: var(--thread-active); color: hsl(var(--success)); }
  .status.inactive { background: var(--thread-inactive); color: hsl(var(--muted-slate)); }

  /* --- queue mode chrome -------------------------------------------------- */
  .qbar {
    display: none; flex: 0 0 auto; max-height: 40%; overflow-y: auto;
    padding: 9px 14px; gap: 8px;
    background: hsl(var(--soft-background)); border-bottom: 1px solid hsl(var(--border));
    flex-direction: column;
  }
  .qbar.on { display: flex; }
  .instructions-wrap {
    display: flex; align-items: flex-start; gap: 8px;
    border-left: 2px solid hsl(var(--primary)); padding: 2px 0 2px 9px;
  }
  .instructions {
    flex: 1 1 auto; font-size: 12px; color: hsl(var(--foreground));
    display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2;
    overflow: hidden;
  }
  .instructions.open { display: block; max-height: 160px; overflow-y: auto; }
  .instructions-toggle {
    flex: 0 0 auto; font-size: 11px; padding: 1px 6px; color: hsl(var(--muted-slate));
    border: 1px solid hsl(var(--border)); border-radius: 999px;
  }
  .instructions-toggle:hover { color: hsl(var(--primary)); }
  .qnav { display: flex; align-items: center; gap: 8px; }
  .progress {
    flex: 1 1 auto; height: 5px; border-radius: 999px; overflow: hidden;
    background: hsl(var(--muted-disabled));
  }
  .progress > i { display: block; height: 100%; width: 0; background: hsl(var(--primary)); transition: width .25s; }
  .dots { display: flex; gap: 4px; flex-wrap: wrap; }
  .dot {
    width: 18px; height: 18px; border-radius: 5px; font-size: 10px;
    display: inline-flex; align-items: center; justify-content: center;
    background: hsl(var(--muted-disabled)); color: hsl(var(--muted-slate));
  }
  .dot.done { background: hsl(var(--success) / .18); color: hsl(var(--success)); }
  .dot.current { outline: 2px solid hsl(var(--primary)); outline-offset: 1px; }
  .def { display: flex; align-items: center; gap: 7px; }
  .def-name { font-size: 12px; font-weight: 500; }
  .cat-btn {
    font-size: 12px; padding: 4px 10px; border: 1px solid hsl(var(--border));
    border-radius: 999px;
  }
  .cat-btn:hover { background: hsl(var(--muted-disabled)); }
  .cat-btn.on { background: hsl(var(--primary)); color: #fff; border-color: transparent; }

  /* --- transcript (TraceMessage.tsx) ------------------------------------- */
  /* The only scroller in the card: it absorbs whatever the header and footer leave,
     and `min-height: 0` lets it give way when the instructions banner is expanded. */
  .transcript { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 14px 16px 6px; }
  .turn { display: flex; flex-direction: column; gap: 8px; padding: 4px 0 16px; }
  .turn.flagged {
    background: var(--warning-soft); margin: 0 -16px; padding: 12px 16px 16px;
    border-left: 2px solid var(--warning-line);
  }
  .row-user { display: flex; justify-content: flex-end; padding-left: 48px; }
  .bubble {
    background: var(--message-input-background);
    border-radius: 14px 14px 2px 14px; padding: 9px 14px;
    white-space: pre-wrap; overflow-wrap: anywhere;
  }
  .row-assistant { display: flex; flex-direction: column; gap: 5px; padding-right: 48px; }
  .answer { white-space: pre-wrap; overflow-wrap: anywhere; }
  .actions {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
    opacity: .75; transition: opacity .12s;
  }
  .turn:hover .actions { opacity: 1; }
  .sep { width: 1px; height: 12px; background: hsl(var(--border)); margin: 0 2px; }

  button { font: inherit; cursor: pointer; border-radius: 7px; border: 1px solid transparent;
           background: transparent; color: inherit; }
  button:disabled { opacity: .55; cursor: default; }
  .icon-btn {
    display: inline-flex; align-items: center; justify-content: center;
    width: 26px; height: 24px; color: hsl(var(--muted-slate)); transition: all .12s;
  }
  .icon-btn:hover { background: hsl(var(--muted-disabled)); color: hsl(var(--foreground)); }
  .icon-btn.on { color: hsl(var(--primary)); background: hsl(var(--primary) / .1); }
  .icon-btn.on.down { color: hsl(var(--destructive)); background: hsl(var(--destructive) / .1); }
  .link {
    display: inline-flex; align-items: center; gap: 3px; font-size: 12px;
    color: hsl(var(--muted-slate)); padding: 2px 7px;
  }
  .link:hover { color: hsl(var(--primary)); background: hsl(var(--muted-disabled)); }
  .score-pill {
    font-size: 11px; padding: 2px 8px; border-radius: 999px;
    border: 1px solid hsl(var(--border)); color: hsl(var(--muted-slate));
  }

  /* --- skeleton --------------------------------------------------------- */
  .sk { border-radius: 8px; background: hsl(var(--skeleton)); position: relative; overflow: hidden; }
  .sk::after {
    content: ""; position: absolute; inset: 0; transform: translateX(-100%);
    background: linear-gradient(90deg, transparent, hsl(var(--skeleton-shine) / .55), transparent);
    animation: shimmer 1.4s infinite;
  }
  @keyframes shimmer { 100% { transform: translateX(100%); } }
  .sk-turn { display: flex; flex-direction: column; gap: 10px; padding-bottom: 20px; }
  .sk-user { align-self: flex-end; height: 34px; width: 58%; border-radius: 14px 14px 2px 14px; }
  .sk-line { height: 12px; }

  /* --- footer ----------------------------------------------------------- */
  footer {
    flex: 0 0 auto; border-top: 1px solid hsl(var(--border));
    background: hsl(var(--soft-background)); padding: 11px 16px 13px;
    display: flex; flex-direction: column; gap: 9px;
  }
  .f-row { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
  input[type="text"], textarea {
    font: inherit; color: inherit; background: hsl(var(--background));
    border: 1px solid hsl(var(--border)); border-radius: 7px; padding: 6px 9px;
  }
  input::placeholder, textarea::placeholder { color: hsl(var(--light-slate)); }
  input[type="text"]:focus, textarea:focus {
    outline: 2px solid hsl(var(--primary) / .35); outline-offset: -1px;
  }
  textarea { resize: vertical; min-height: 36px; width: 100%; }
  input[type="range"] { accent-color: hsl(var(--primary)); width: 120px; }
  .readout {
    font-size: 12px; font-weight: 500; min-width: 30px; text-align: center;
    padding: 2px 6px; border-radius: 5px; background: hsl(var(--muted-disabled));
  }
  .btn-primary { background: hsl(var(--primary)); color: #fff; padding: 7px 15px; font-weight: 500; }
  .btn-primary:hover:not(:disabled) { background: hsl(var(--primary-hover)); }
  .btn-ghost { border: 1px solid hsl(var(--border)); padding: 7px 13px; }
  .btn-ghost:hover:not(:disabled) { background: hsl(var(--muted-disabled)); }
  .spinner {
    display: none; width: 12px; height: 12px; margin-right: 7px; vertical-align: -1px;
    border: 2px solid currentColor; border-right-color: transparent; border-radius: 999px;
    animation: spin .7s linear infinite;
  }
  @keyframes spin { 100% { transform: rotate(360deg); } }
  button.busy .spinner { display: inline-block; }
  .spacer { flex: 1 1 auto; }
  .toast { font-size: 12px; min-height: 16px; transition: color .15s; }
  .toast.ok { color: hsl(var(--success)); }
  .toast.err { color: hsl(var(--destructive)); }
  .center { padding: 34px 16px; text-align: center; color: hsl(var(--muted-slate)); }
  .f-row.done { align-items: flex-start; gap: 10px; }
  .done-mark {
    display: inline-flex; align-items: center; justify-content: center;
    width: 26px; height: 26px; flex: 0 0 auto; border-radius: 999px;
    background: hsl(var(--success) / .14); color: hsl(var(--success));
  }

  /* Message trace: diagnostics only, never shown on its own. Double-click the
     logo to reveal it when a host renders the app without devtools. */
  #diag {
    display: none; padding: 6px 14px; border-top: 1px solid hsl(var(--border));
    background: hsl(var(--muted-disabled)); color: hsl(var(--muted-slate));
    font-family: ui-monospace, monospace; font-size: 10px; white-space: pre-wrap;
    max-height: 110px; overflow-y: auto;
  }
  #diag.on { display: block; }
</style>
</head>
<body>
<div class="card">
  <div class="accent"></div>

  <div class="brand">
    <div class="logo" id="logo" title="Opik"></div>
    <div class="brand-sep"></div>
    <span class="t-title">Thread review</span>
    <div class="brand-right">
      <span class="t-xs muted" id="totals"></span>
      <button class="link" id="open-opik" hidden>Open in Opik
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round"><path d="M7 17 17 7M8 7h9v9"/></svg>
      </button>
    </div>
  </div>

  <div class="meta">
    <div class="thread-icon" aria-hidden="true">
      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 9a2 2 0 0 1-2 2H6l-4 4V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2Z"/>
        <path d="M18 9h2a2 2 0 0 1 2 2v11l-4-4h-6a2 2 0 0 1-2-2v-1"/>
      </svg>
    </div>
    <span class="id-chip mono" id="thread-id">loading…</span>
    <span class="t-xs muted" id="date-range"></span>
    <span class="status" id="status" hidden></span>
  </div>

  <div class="qbar" id="qbar">
    <div class="instructions-wrap">
      <div class="instructions" id="instructions"></div>
      <button class="instructions-toggle" id="instructions-toggle" hidden>more</button>
    </div>
    <div class="qnav">
      <span class="t-xs-acc" id="qprogress-label">0 / 0 reviewed</span>
      <div class="progress"><i id="qprogress"></i></div>
      <div class="dots" id="qdots"></div>
      <button class="btn-ghost" id="btn-prev" style="padding:3px 9px">←</button>
      <button class="btn-ghost" id="btn-next" style="padding:3px 9px">→</button>
    </div>
  </div>

  <div class="transcript" id="transcript">
    <div id="skeleton">
      <div class="sk-turn">
        <div class="sk sk-user"></div>
        <div class="sk sk-line" style="width:92%"></div>
        <div class="sk sk-line" style="width:64%"></div>
      </div>
      <div class="sk-turn">
        <div class="sk sk-user" style="width:44%"></div>
        <div class="sk sk-line" style="width:78%"></div>
      </div>
      <div class="sk-turn">
        <div class="sk sk-user" style="width:36%"></div>
        <div class="sk sk-line" style="width:88%"></div>
        <div class="sk sk-line" style="width:52%"></div>
      </div>
    </div>
  </div>

  <footer>
    <div class="f-row" id="score-row">
      <span class="t-xs-acc">Thread score</span>
      <input type="text" id="score-name" value="User feedback" list="score-names"
             style="width:148px" aria-label="Score name" />
      <datalist id="score-names">
        <option>User feedback</option>
        <option>user_frustration</option>
        <option>helpfulness</option>
        <option>correctness</option>
      </datalist>
      <input type="range" id="score-value" min="0" max="1" step="0.1" value="0.5" />
      <span class="readout mono" id="score-readout">0.5</span>
      <input type="text" id="score-reason" placeholder="reason (optional)" style="flex:1 1 150px" />
    </div>
    <div class="f-row">
      <textarea id="comment" rows="1" placeholder="Comment for the team…"></textarea>
    </div>
    <div class="f-row">
      <span class="toast" id="toast"></span>
      <span class="spacer"></span>
      <button class="btn-ghost" id="btn-close-thread"><span class="spinner"></span>Close thread</button>
      <button class="btn-ghost" id="btn-finish" hidden><span class="spinner"></span>Finish review</button>
      <button class="btn-primary" id="btn-save"><span class="spinner"></span>Save annotation</button>
    </div>
  </footer>

  <div id="diag"></div>
</div>

<script>
(function () {
  "use strict";

  /* ---------- ext-apps postMessage bridge (no dependencies) -------------- */
  var nextId = 1;
  var pending = new Map();
  var toolArgs = null;
  var initialized = false;
  var state = { mode: "thread", threadId: null, project: {}, messages: [], thread: {},
               url: null, queue: {}, definitions: [], items: [], index: 0 };

  function diag(line) {
    var el = document.getElementById("diag");
    if (el) { el.textContent += line + "\n"; el.scrollTop = el.scrollHeight; }
  }
  window.onerror = function (m, s, l) { diag("js error: " + m + " @" + l); };

  function send(msg) { window.parent.postMessage(msg, "*"); }

  function request(method, params) {
    var id = nextId++;
    return new Promise(function (resolve, reject) {
      pending.set(id, { resolve: resolve, reject: reject });
      send({ jsonrpc: "2.0", id: id, method: method, params: params || {} });
    });
  }

  function notify(method, params) {
    send({ jsonrpc: "2.0", method: method, params: params || {} });
  }

  // Every write goes through opik-mcp's universal `write` tool, so the app has no
  // private write surface: same operations, same validation, same audit trail as
  // when the model does it.
  function write(operation, data) {
    return callTool("write", { operation: operation, data: data }).then(function (res) {
      if (res && res.error) throw new Error(res.error.message || res.error.kind || "write failed");
      return res;
    });
  }

  function threadScope() {
    return state.queue.project_id
      ? { project_id: state.queue.project_id }
      : (state.project.id ? { project_id: state.project.id }
                          : { project_name: state.project.name });
  }

  // Two different jobs, two different methods:
  //   ui/update-model-context — hands the model the verdict silently, for later turns
  //   ui/message             — a visible user utterance, which is what makes the agent
  //                            take a turn *now*
  // Reviewing without the first one means the agent has no idea what the human decided
  // until it re-reads the queue; using only the second means every save would spam the
  // transcript.
  function pushModelContext() {
    var payload = state.mode === "queue" ? queueVerdict() : threadVerdict();
    if (!payload) return Promise.resolve();
    return request("ui/update-model-context", {
      content: [{ type: "text", text: payload.summary }],
      structuredContent: payload
    }).catch(function (e) { diag("update-model-context unsupported: " + e.message); });
  }

  // "Bad" is relative to the rubric that asked the question: the bottom half of a
  // numerical range, or the lowest-valued option of a categorical/boolean definition.
  // A flat `<= 0.5` would invert the verdict for any rubric where low means good.
  function isBadScore(name, value) {
    if (typeof value !== "number") return false;
    var def = null;
    for (var i = 0; i < (state.definitions || []).length; i++) {
      if (state.definitions[i].name === name) { def = state.definitions[i]; break; }
    }
    if (!def) return false;
    var details = def.details || {};
    var values;
    if (def.type === "numerical") {
      values = [details.min, details.max];
    } else if (def.type === "boolean") {
      values = [0, 1];
    } else {
      values = Object.keys(details.categories || {}).map(function (k) {
        return details.categories[k];
      });
    }
    values = values.filter(function (v) { return typeof v === "number"; });
    if (!values.length) return false;
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
    if (hi === lo) return false;
    return (value - lo) / (hi - lo) <= 0.5;
  }

  function isFlaggedItem(item) {
    var scores = item.scores || {};
    return Object.keys(scores).some(function (k) { return isBadScore(k, scores[k]); });
  }

  function queueVerdict() {
    if (!state.queue.id) return null;
    var reviewed = state.items.filter(function (it) { return it.reviewed; });
    var flagged = state.items.filter(isFlaggedItem).map(function (it) { return it.thread_id; });
    return {
      queue_id: state.queue.id, queue_name: state.queue.name,
      project_name: state.queue.project_name || null,
      reviewed_count: reviewed.length, total: state.items.length,
      flagged_thread_ids: flagged,
      items: state.items.map(function (it) {
        return { thread_id: it.thread_id, scores: it.scores || {}, reviewed: !!it.reviewed };
      }),
      summary: "Human review of queue '" + (state.queue.name || "") + "': " +
        reviewed.length + " of " + state.items.length + " threads scored" +
        (flagged.length ? "; flagged " + flagged.join(", ") : "") + "."
    };
  }

  function threadVerdict() {
    if (!state.threadId) return null;
    var scores = {};
    (state.messages || []).forEach(function (m, i) {
      var v = userFeedbackValue(m.feedback_scores);
      if (v !== null) scores["turn " + (i + 1)] = v;
    });
    return {
      thread_id: state.threadId, project_name: state.project.name || null,
      turn_scores: scores, thread_scores: state.savedThreadScores || {},
      summary: "Human review of thread " + state.threadId + ": " +
        JSON.stringify(Object.assign({}, scores, state.savedThreadScores || {})) + "."
    };
  }

  function callTool(name, args) {
    return request("tools/call", { name: name, arguments: args }).then(function (res) {
      if (res && res.isError) throw new Error(textFromContent(res.content) || "tool call failed");
      if (res && res.structuredContent) return res.structuredContent;
      var txt = textFromContent(res && res.content);
      try { return JSON.parse(txt); } catch (e) { return txt; }
    });
  }

  function textFromContent(content) {
    if (!content) return "";
    for (var i = 0; i < content.length; i++) {
      if (content[i] && content[i].type === "text") return content[i].text;
    }
    return "";
  }

  window.addEventListener("message", function (event) {
    var msg = event.data;
    if (!msg || typeof msg !== "object") return;
    // Match replies on id alone rather than requiring jsonrpc:"2.0", so a host
    // that trims the envelope can't wedge the app.
    if (msg.id !== undefined && pending.has(msg.id)) {
      var p = pending.get(msg.id);
      pending.delete(msg.id);
      diag("← reply#" + msg.id + (msg.error ? " error" : " ok"));
      if (msg.error) p.reject(new Error(msg.error.message || "host error"));
      else p.resolve(msg.result === undefined ? {} : msg.result);
      return;
    }
    if (!msg.method) return;
    diag("← " + msg.method);

    if (msg.method === "ui/notifications/tool-input") {
      toolArgs = (msg.params && msg.params.arguments) || null;
      loadThread();
    } else if (msg.method === "ui/notifications/tool-result") {
      loadThread();
    } else if (msg.method === "ui/notifications/host-context-changed") {
      var t = msg.params && msg.params.hostContext && msg.params.hostContext.theme;
      if (t) document.documentElement.setAttribute("data-theme", t === "dark" ? "dark" : "light");
    }
  });

  // Constant by design: the card is a fixed height, so the number we ask for always
  // matches what we draw. Nothing here measures content, so nothing can drift.
  var CARD_HEIGHT = parseInt(
    getComputedStyle(document.documentElement).getPropertyValue("--card-height"), 10) || 620;

  function reportSize() {
    notify("ui/notifications/size-changed", {
      width: document.documentElement.scrollWidth,
      height: CARD_HEIGHT + 6
    });
  }

  function markInitialized() {
    if (initialized) return;
    initialized = true;
    notify("ui/notifications/initialized", {});
    reportSize();
    // The host holds tool-input/tool-result until it sees `initialized`. Try anyway:
    // app_data falls back to the entity the model just read, so the UI fills
    // in even if no notification ever arrives.
    setTimeout(function () { if (!state.loaded) loadThread(); }, 500);
  }

  request("ui/initialize", {
    protocolVersion: "2026-01-26",
    capabilities: { appCapabilities: { availableDisplayModes: ["inline", "fullscreen"] } },
    clientInfo: { name: "Opik Thread Review", version: "0.1.0" }
  }).then(function (res) {
    var theme = res && res.hostContext && res.hostContext.theme;
    diag("host: " + ((res && res.hostInfo && res.hostInfo.name) || "?") + " theme=" + (theme || "?"));
    document.documentElement.setAttribute("data-theme", theme === "dark" ? "dark" : "light");
    markInitialized();
  }).catch(function (e) {
    diag("ui/initialize failed: " + e.message);
    markInitialized();
  });
  setTimeout(markInitialized, 700);

  /* ---------- data ------------------------------------------------------- */
  // One app, two entry points: a single thread (thread_review) or a whole
  // annotation queue (annotation_queue_review). The tool arguments decide.
  function loadThread() {
    if (state.loading || state.loaded) return;
    var args = toolArgs || {};
    // read() is generic, so the app follows whatever entity the tool was called
    // with. entity_type/id come straight from the read() arguments.
    var type = args.entity_type || (args.queue ? "annotation_queue" : "thread");
    var id = args.id || args.queue || args.thread || args.thread_id || "";
    if (type === "annotation_queue") return loadQueue(id, !id);
    return loadSingleThread({ id: id, project_id: args.project_id,
                              project_name: args.project_name }, !id);
  }

  // One loader for both entry points. `viaFallback` marks the speculative attempt a
  // host's preload triggers before the tool has run: losing that race is expected, so
  // it must never flash an error at the reviewer.
  function load(entityType, args, viaFallback, apply) {
    if (state.loading || state.loaded) return;
    state.loading = true;
    diag("→ app_data(" + entityType + (viaFallback ? ", server fallback" : "") + ")");
    callTool("app_data", {
      entity_type: entityType,
      id: args.id || "",
      project_name: args.project_name || null,
      project_id: args.project_id || null
    }).then(function (data) {
      state.loading = false;
      if (!apply(data)) {
        if (!viaFallback) fail("The server returned no " + entityType + " data.");
        return;
      }
      state.loaded = true;
    }).catch(function (e) {
      state.loading = false;
      diag((viaFallback ? "fallback " : "") + "load failed: " + e.message);
      if (!viaFallback) fail(e.message);
    });
  }

  function loadSingleThread(args, viaFallback) {
    load("thread", args, viaFallback, function (data) {
      if (!data || typeof data !== "object" || !data.thread_id) return false;
      applyThread(data);
      render();
      return true;
    });
  }

  function applyThread(data) {
    state.threadId = data.thread_id;
    state.thread = data.thread || {};
    state.messages = data.messages || [];
    state.project = data.project || {};
    state.url = data.url || null;
  }

  function loadQueue(queue, viaFallback) {
    load("annotation_queue", { id: queue }, viaFallback, function (data) {
      if (!data || !data.queue) return false;
      applyQueue(data);
      return true;
    });
  }

  function applyQueue(data) {
    state.mode = "queue";
    state.queue = data.queue || {};
    state.definitions = data.definitions || [];
    state.items = data.items || [];
    state.queueUrl = data.url || null;
    renderQueueChrome();
    var first = state.items.findIndex(function (it) { return !it.reviewed; });
    showItem(first === -1 ? 0 : first);
  }

  function showItem(index) {
    if (!state.items.length) { fail("This queue has no items."); return; }
    state.index = Math.max(0, Math.min(index, state.items.length - 1));
    var item = state.items[state.index];
    renderQueueChrome();
    document.getElementById("transcript").innerHTML =
      '<div class="center">Loading thread ' + esc(item.thread_id) + "…</div>";
    callTool("app_data", {
      entity_type: "thread", id: item.thread_id,
      project_id: state.queue.project_id || null
    }).then(function (data) {
      if (!data || !data.thread_id) { fail("Could not load this thread."); return; }
      applyThread(data);
      render();
      restoreDefinitionValues(item);
    }).catch(function (e) { fail(e.message); });
  }

  /* ---------- rendering -------------------------------------------------- */
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // Turn payloads are arbitrary JSON. Prefer the shapes Opik's own UI expects,
  // fall back to pretty JSON rather than showing nothing.
  function textOf(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "string") return value;
    if (Array.isArray(value)) {
      var last = value[value.length - 1];
      return last && typeof last === "object" ? textOf(last.content || last) : String(last);
    }
    if (typeof value === "object") {
      if (Array.isArray(value.messages) && value.messages.length) {
        return textOf(value.messages[value.messages.length - 1]);
      }
      var keys = ["content", "output", "answer", "response", "text", "input", "question", "query"];
      for (var i = 0; i < keys.length; i++) {
        if (typeof value[keys[i]] === "string") return value[keys[i]];
      }
      try { return JSON.stringify(value, null, 2); } catch (e) { return String(value); }
    }
    return String(value);
  }

  function fmtDuration(ms) {
    if (typeof ms !== "number") return null;
    return ms >= 1000 ? (ms / 1000).toFixed(1) + "s" : Math.round(ms) + "ms";
  }

  function fmtTokens(usage) {
    if (!usage) return null;
    var total = usage.total_tokens || usage.completion_tokens
      || (usage.original_usage && usage.original_usage.total_tokens);
    if (!total) return null;
    return total >= 1000 ? (total / 1000).toFixed(1) + "k tok" : total + " tok";
  }

  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return d.toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
    });
  }

  function userFeedbackValue(scores) {
    var list = scores || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i] && list[i].name === "User feedback") return list[i].value;
    }
    return null;
  }

  function isFlagged(m) {
    return Boolean(m.error_info) || textOf(m.output).trim().length === 0;
  }

  var THUMB_UP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/></svg>';
  var THUMB_DOWN = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 14V2"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"/></svg>';
  var ARROW = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M7 17 17 7M8 7h9v9"/></svg>';
  var CHECK = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
  var WARN = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px"><path d="m21.7 18-8-14a2 2 0 0 0-3.4 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>';

  function render() {
    var t = state.thread || {};
    document.getElementById("thread-id").textContent = state.threadId;

    var start = fmtDate(t.start_time), end = fmtDate(t.end_time);
    document.getElementById("date-range").textContent =
      start && end && start !== end ? start + " – " + end.split(", ").pop() : start;

    var statusEl = document.getElementById("status");
    var status = (t.status || "").toLowerCase();
    if (status) {
      statusEl.hidden = false;
      statusEl.className = "status " + (status === "active" ? "active" : "inactive");
      statusEl.textContent = status;
    }

    var cost = 0, turns = state.messages.length;
    state.messages.forEach(function (m) { cost += m.total_estimated_cost || 0; });
    document.getElementById("totals").textContent =
      turns + (turns === 1 ? " turn" : " turns") + (cost ? " · $" + cost.toFixed(4) : "");

    if (state.url) {
      var btn = document.getElementById("open-opik");
      btn.hidden = false;
      btn.onclick = function () { request("ui/open-link", { url: state.url }); };
    }

    var host = document.getElementById("transcript");
    if (!turns) {
      host.innerHTML = '<div class="center">This thread has no turns.</div>';
      reportSize();
      return;
    }

    host.innerHTML = state.messages.map(function (m, i) {
      var fb = userFeedbackValue(m.feedback_scores);
      var bits = [];
      var dur = fmtDuration(m.duration);
      if (dur) bits.push(esc(dur));
      if (m.total_estimated_cost) bits.push("$" + m.total_estimated_cost.toFixed(4));
      var tok = fmtTokens(m.usage);
      if (tok) bits.push(esc(tok));

      var otherScores = (m.feedback_scores || []).filter(function (s) {
        return s.name !== "User feedback";
      }).map(function (s) {
        return '<span class="score-pill">' + esc(s.name) + " " + esc(s.value) + "</span>";
      }).join("");

      var answer = m.error_info
        ? '<span style="color:hsl(var(--destructive))">' + WARN + " " + esc(textOf(m.error_info)) + "</span>"
        : (esc(textOf(m.output)) || '<span class="muted">' + WARN + " no response</span>");

      return '' +
        '<div class="turn' + (isFlagged(m) ? " flagged" : "") + '">' +
          '<div class="row-user"><div class="bubble">' + esc(textOf(m.input)) + '</div></div>' +
          '<div class="row-assistant">' +
            '<div class="answer">' + answer + '</div>' +
            '<div class="actions">' +
              '<button class="icon-btn' + (fb === 1 ? " on" : "") + '" data-turn="' + i +
                '" data-value="1" title="Good answer">' + THUMB_UP + '</button>' +
              '<button class="icon-btn down' + (fb === 0 ? " on" : "") + '" data-turn="' + i +
                '" data-value="0" title="Bad answer">' + THUMB_DOWN + '</button>' +
              '<span class="sep"></span>' +
              '<span class="t-xs muted">' + bits.join(" · ") + '</span>' +
              otherScores +
              '<button class="link" data-trace="' + esc(m.trace_id || "") + '">trace ' + ARROW + '</button>' +
            '</div>' +
          '</div>' +
        '</div>';
    }).join("");

    host.querySelectorAll("button[data-turn]").forEach(function (b) {
      b.addEventListener("click", function () {
        onTurnFeedback(parseInt(b.getAttribute("data-turn"), 10),
                       parseFloat(b.getAttribute("data-value")), b);
      });
    });
    host.querySelectorAll("button[data-trace]").forEach(function (b) {
      b.addEventListener("click", function () {
        var url = traceUrl(b.getAttribute("data-trace"));
        if (url) request("ui/open-link", { url: url });
      });
    });
    reportSize();
  }

  function traceUrl(traceId) {
    if (!state.url || !traceId) return null;
    return state.url.replace(/logsType=threads&thread=[^&]*/, "logsType=traces&trace=" + traceId);
  }

  /* ---------- queue mode ------------------------------------------------- */
  function renderQueueChrome() {
    var q = state.queue || {};
    document.getElementById("qbar").classList.add("on");
    document.getElementById("btn-close-thread").hidden = true;
    document.getElementById("btn-finish").hidden = false;
    document.querySelector(".t-title").textContent = q.name || "Annotation queue";
    var instructions = document.getElementById("instructions");
    var toggle = document.getElementById("instructions-toggle");
    instructions.textContent = q.instructions || "No instructions were given for this queue.";
    // Only offer the toggle when the text is actually cut off.
    toggle.hidden = instructions.scrollHeight <= instructions.clientHeight + 1;
    if (!toggle.dataset.wired) {
      toggle.dataset.wired = "1";
      toggle.addEventListener("click", function () {
        var open = instructions.classList.toggle("open");
        toggle.textContent = open ? "less" : "more";
        reportSize();
      });
    }

    var done = state.items.filter(function (it) { return it.reviewed; }).length;
    var total = state.items.length;
    document.getElementById("qprogress-label").textContent = done + " / " + total + " reviewed";
    document.getElementById("qprogress").style.width = total ? (done / total * 100) + "%" : "0";

    document.getElementById("qdots").innerHTML = state.items.map(function (it, i) {
      return '<button class="dot' + (it.reviewed ? " done" : "") +
        (i === state.index ? " current" : "") + '" data-item="' + i +
        '" title="' + esc(it.thread_id) + '">' + (i + 1) + "</button>";
    }).join("");
    document.querySelectorAll("#qdots .dot").forEach(function (d) {
      d.addEventListener("click", function () {
        showItem(parseInt(d.getAttribute("data-item"), 10));
      });
    });

    renderDefinitions();
  }

  // The queue dictates which scores the reviewer is asked for, so the controls are
  // generated from its feedback definitions instead of a free-form score box.
  function renderDefinitions() {
    var row = document.getElementById("score-row");
    if (!state.definitions.length) {
      row.innerHTML = '<span class="t-xs muted">This queue asks for comments only.</span>';
      return;
    }
    row.innerHTML = state.definitions.map(function (d) {
      var details = d.details || {};
      var name = d.name;
      if (d.type === "numerical") {
        var min = details.min != null ? details.min : 0;
        var max = details.max != null ? details.max : 1;
        var step = (max - min) <= 1 ? 0.1 : 1;
        return '<div class="def" data-def="' + esc(name) + '" data-kind="numerical">' +
          '<span class="def-name">' + esc(name) + "</span>" +
          '<input type="range" min="' + min + '" max="' + max + '" step="' + step +
          '" value="' + min + '" />' +
          '<span class="readout mono">–</span></div>';
      }
      var options = d.type === "boolean"
        ? [[details.true_label || "yes", 1], [details.false_label || "no", 0]]
        : Object.keys(details.categories || {}).map(function (k) {
            return [k, details.categories[k]];
          }).sort(function (a, b) { return a[1] - b[1]; });
      return '<div class="def" data-def="' + esc(name) + '" data-kind="choice">' +
        '<span class="def-name">' + esc(name) + "</span>" +
        options.map(function (o) {
          return '<button class="cat-btn" data-value="' + o[1] + '">' + esc(o[0]) + "</button>";
        }).join("") + "</div>";
    }).join("");

    row.querySelectorAll('.def[data-kind="numerical"] input').forEach(function (input) {
      input.addEventListener("input", function () {
        input.parentElement.querySelector(".readout").textContent = input.value;
        input.dataset.touched = "1";
      });
    });
    row.querySelectorAll(".cat-btn").forEach(function (b) {
      b.addEventListener("click", function () {
        b.parentElement.querySelectorAll(".cat-btn").forEach(function (o) {
          o.classList.remove("on");
        });
        b.classList.add("on");
      });
    });
  }

  function restoreDefinitionValues(item) {
    var existing = item.scores || {};
    document.querySelectorAll("#score-row .def").forEach(function (def) {
      var name = def.getAttribute("data-def");
      var value = existing[name];
      if (value === undefined) return;
      if (def.getAttribute("data-kind") === "numerical") {
        var input = def.querySelector("input");
        input.value = value;
        input.dataset.touched = "1";
        def.querySelector(".readout").textContent = value;
      } else {
        def.querySelectorAll(".cat-btn").forEach(function (b) {
          b.classList.toggle("on", parseFloat(b.getAttribute("data-value")) === value);
        });
      }
    });
  }

  function collectScores() {
    var out = [];
    document.querySelectorAll("#score-row .def").forEach(function (def) {
      var name = def.getAttribute("data-def");
      if (def.getAttribute("data-kind") === "numerical") {
        var input = def.querySelector("input");
        if (input.dataset.touched) out.push({ name: name, value: parseFloat(input.value) });
      } else {
        var on = def.querySelector(".cat-btn.on");
        if (on) out.push({ name: name, value: parseFloat(on.getAttribute("data-value")) });
      }
    });
    return out;
  }

  document.getElementById("btn-prev").addEventListener("click", function () {
    showItem(state.index - 1);
  });
  document.getElementById("btn-next").addEventListener("click", function () {
    showItem(state.index + 1);
  });

  // The point of the whole loop: hand the verdict back to the agent so it can act
  // on it without the human retyping anything.
  document.getElementById("btn-finish").addEventListener("click", function () {
    var btn = this;
    var verdict = queueVerdict() || { reviewed_count: 0, total: 0, flagged_thread_ids: [] };
    // The data travels as context; the message only has to start the agent's turn, so
    // keep it as short as a person would actually type.
    var text = "Done reviewing — " + verdict.reviewed_count + " of " + verdict.total +
      " scored" + (verdict.flagged_thread_ids.length
        ? ", " + verdict.flagged_thread_ids.length + " flagged" : "") +
      ". What do we fix?";
    // Claude Desktop validates `content` as an array; the ext-apps spec example shows a
    // bare object. Send the array and fall back, so either reading works.
    var block = { type: "text", text: text };
    busy(btn, true);
    pushModelContext()
      .then(function () {
        // Claude Desktop validates `content` as an array; the spec example shows a bare
        // object. Send the array and fall back, so either reading works.
        return request("ui/message", { role: "user", content: [block] })
          .catch(function () { return request("ui/message", { role: "user", content: block }); });
      })
      .then(function () { renderFinished(verdict); })
      .catch(function (e) {
        busy(btn, false);
        toast(e.message || "the host rejected the message", "err");
      });
  });

  /* ---------- actions --------------------------------------------------- */
  function toast(msg, kind) {
    var el = document.getElementById("toast");
    el.textContent = msg;
    el.className = "toast " + (kind || "");
    if (kind === "ok") setTimeout(function () { if (el.textContent === msg) el.textContent = ""; }, 3500);
  }

  function busy(btn, on) {
    btn.disabled = on;
    btn.classList.toggle("busy", on);
  }

  function onTurnFeedback(index, value, btn) {
    var m = state.messages[index];
    if (!m || !m.trace_id) return;
    var row = btn.parentElement;
    var was = row.querySelector(".icon-btn.on");
    row.querySelectorAll("button[data-turn]").forEach(function (b) { b.classList.remove("on"); });
    btn.classList.add("on");
    btn.disabled = true;
    write("score.create", { target: "trace", target_id: m.trace_id,
                            name: "User feedback", value: value })
      .then(function () {
        m.feedback_scores = (m.feedback_scores || []).filter(function (s) {
          return s.name !== "User feedback";
        }).concat([{ name: "User feedback", value: value }]);
        toast("Turn " + (index + 1) + ": User feedback = " + value, "ok");
        pushModelContext();
      })
      .catch(function (e) {
        btn.classList.remove("on");
        if (was) was.classList.add("on");
        toast(e.message, "err");
      })
      .then(function () { btn.disabled = false; });
  }

  document.getElementById("score-value").addEventListener("input", function (e) {
    document.getElementById("score-readout").textContent = e.target.value;
  });

  document.getElementById("btn-save").addEventListener("click", function () {
    if (state.mode === "queue") return saveQueueItem(this);
    var btn = this;
    var name = document.getElementById("score-name").value.trim();
    var value = parseFloat(document.getElementById("score-value").value);
    var reason = document.getElementById("score-reason").value.trim();
    var comment = document.getElementById("comment").value.trim();
    if (!name) { toast("Score name is required.", "err"); return; }
    if (!state.threadId) { toast("No thread loaded yet.", "err"); return; }
    busy(btn, true);
    toast("Saving…");
    // target='thread' has no singleton route on the backend — always the array form.
    var scope = threadScope();
    var score = Object.assign({ target: "thread", target_id: state.threadId,
                                name: name, value: value }, scope);
    if (reason) score.reason = reason;
    var work = [write("score.create", [score])];
    if (comment) {
      work.push(write("comment.create", Object.assign(
        { target: "thread", target_id: state.threadId, text: comment }, scope)));
    }
    Promise.all(work).then(function () {
      toast("Saved " + name + " = " + value + (comment ? " + comment" : ""), "ok");
      document.getElementById("comment").value = "";
      state.savedThreadScores = state.savedThreadScores || {};
      state.savedThreadScores[name] = value;
      pushModelContext();
    }).catch(function (e) {
      toast(e.message, "err");
    }).then(function () { busy(btn, false); });
  });

  function saveQueueItem(btn) {
    var item = state.items[state.index];
    var scores = collectScores();
    var comment = document.getElementById("comment").value.trim();
    if (!scores.length && !comment) { toast("Score it or leave a comment first.", "err"); return; }
    busy(btn, true);
    toast("Saving…");
    var scope = threadScope();
    var work = scores.map(function (s) {
      return write("score.create", [Object.assign(
        { target: "thread", target_id: item.thread_id, name: s.name, value: s.value }, scope)]);
    });
    if (comment) {
      work.push(write("comment.create", Object.assign(
        { target: "thread", target_id: item.thread_id, text: comment }, scope)));
    }
    Promise.all(work).then(function () {
      item.reviewed = true;
      item.scores = item.scores || {};
      scores.forEach(function (s) { item.scores[s.name] = s.value; });
      document.getElementById("comment").value = "";
      pushModelContext();
      var next = state.items.findIndex(function (it) { return !it.reviewed; });
      toast("Saved. " + (next === -1 ? "Queue complete — hit Finish review." : "Next thread."), "ok");
      if (next === -1) renderQueueChrome(); else showItem(next);
    }).catch(function (e) {
      toast(e.message, "err");
    }).then(function () { busy(btn, false); });
  }

  document.getElementById("btn-close-thread").addEventListener("click", function () {
    var btn = this;
    if (!state.threadId) { toast("No thread loaded yet.", "err"); return; }
    busy(btn, true);
    toast("Closing…");
    write("thread.close", Object.assign({ thread_id: state.threadId }, threadScope()))
      .then(function () {
      var el = document.getElementById("status");
      el.hidden = false; el.className = "status inactive"; el.textContent = "inactive";
      toast("Thread closed — triage recorded in Opik.", "ok");
      busy(btn, false);
      btn.disabled = true;
    }).catch(function (e) {
      toast(e.message, "err");
      busy(btn, false);
    });
  });

  // The panel has to look finished, not just fall silent: the reviewer needs to see
  // what was recorded, and the controls must stop inviting more input.
  function renderFinished(verdict) {
    var flagged = verdict.flagged_thread_ids || [];
    document.querySelector("footer").innerHTML =
      '<div class="f-row done">' +
        '<span class="done-mark">' + CHECK + '</span>' +
        '<div><div class="t-xs-acc">Review complete — handed back to the agent</div>' +
        '<div class="t-xs muted">' + verdict.reviewed_count + " of " + verdict.total +
        " threads scored" + (flagged.length
          ? "; flagged " + flagged.map(esc).join(", ") : "; nothing flagged") +
        '. Scores and comments are in Opik under your name.</div></div>' +
      '</div>';
    var bar = document.getElementById("qprogress");
    if (bar) bar.style.width = "100%";
  }

  function fail(message) {
    document.getElementById("transcript").innerHTML =
      '<div class="center">' + esc(message) + "</div>";
    reportSize();
  }

  // Diagnostics stay out of the way: double-click the logo to see the message trace.
  document.getElementById("logo").addEventListener("dblclick", function () {
    document.getElementById("diag").classList.toggle("on");
    reportSize();
  });

  window.addEventListener("resize", reportSize);
})();
</script>
</body>
</html>
""".replace("__LOGO_LIGHT__", LOGO_LIGHT).replace("__LOGO_DARK__", LOGO_DARK)
