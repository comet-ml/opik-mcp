import logging
import time
from collections.abc import AsyncIterator
from typing import Any, Protocol
from urllib.parse import urlencode

import anyio
from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession
from pydantic import BaseModel

from opik_mcp import audit
from opik_mcp.analytics import EVENT_ASK_OLLIE_COMPLETED, bucket_count
from opik_mcp.analytics.errors import bucket_exception, unwrap_to_real_cause
from opik_mcp.analytics.mcp_client_info import call_context_props
from opik_mcp.comet_client import CometClient, PodDiscovery
from opik_mcp.config import Settings, get_settings, require_ollie_config
from opik_mcp.elicitation import confirm_with_user
from opik_mcp.ollie_client import OllieClient, OllieStreamError, OnTick, SSEEvent
from opik_mcp.writes.registry import WRITE_OPERATIONS

logger = logging.getLogger("opik_mcp.ask_ollie")


def _analytics() -> Any:
    # Lazy attribute fetch so tests can monkeypatch at module scope.
    from opik_mcp.analytics import get_analytics

    return get_analytics()


FOOTER_MAX_ENTRIES = 5


# Allowlist for the ``auto_approval_tools`` analytics field. ``target_tool``
# arrives in the pod's ``confirm_required`` SSE frame (``tool_name``) and is
# host/pod-controlled free text, so it must never reach analytics verbatim —
# the privacy contract is bucketed enums only. Names on the write-operation
# allowlist pass through; anything else collapses to ``"other"``.
_KNOWN_TARGET_TOOLS: frozenset[str] = frozenset(WRITE_OPERATIONS)


def _bucket_auto_approval_tools(details: list[tuple[str | None, str | None]]) -> str:
    """Comma-join the distinct auto-approved tool names, allowlisted to known
    write operations (unknown / pod-supplied names → ``"other"``)."""
    buckets = {(t if t in _KNOWN_TARGET_TOOLS else "other") for t, _ in details if t}
    return ",".join(sorted(buckets))


# Session-scoped allowlist of pod target_tool names that the user has
# previously opted into via the elicit form's "always_approve" toggle.
# Scope = the MCP subprocess lifetime (= one host connection). Reconnect
# resets the set. Keyed by `target_tool` (e.g. "comment.create"), NOT by
# tool_use_id or entity id — the semantics match ollie-assist's
# "Yes, during this session" button. A None target_tool is never added
# because we'd have no stable key to match future events against.
#
# This is intentionally process-global rather than per-ask_ollie-call:
# ask_ollie is invoked many times over a single host session, and the
# user's whole point in flipping the toggle is to avoid re-prompting on
# the NEXT call too.
_SESSION_ALLOWLIST: set[str] = set()


def _reset_session_allowlist_for_tests() -> None:
    """Clear the process-global allowlist. Test-only entry point.

    Production code must never call this — the set's lifetime is
    deliberately tied to the subprocess so a reconnect is the only way
    a user can withdraw a previously-granted "always approve" decision.
    """
    _SESSION_ALLOWLIST.clear()


def _format_elicit_prompt(target_tool: str | None, summary: str | None) -> str:
    """Build the elicitation prompt body.

    Leads with the tool name so the user can see at a glance what's
    being asked. Summary (when present) follows on its own line so it
    stays legible in CC's rendering. Closing line tells the user how
    the optional toggle behaves so they don't have to discover it.
    """
    tool_line = target_tool or "<unknown tool>"
    body = f"Ollie wants to run tool: {tool_line}"
    if summary:
        body = f"{body}\n\nSummary: {summary}"
    body = (
        f"{body}\n\nToggle 'always_approve' to allow all future calls "
        "to this tool in the current session."
    )
    return body


def _format_approval_entry(target_tool: str | None, summary: str | None) -> str:
    """Render one auto-approval as `"<tool> (<summary>)"` (or fallbacks).

    The footer must remain readable even when the pod ships an event missing
    tool_name or summary, so we degrade gracefully instead of printing "None".
    """
    tool_label = target_tool or "<unknown tool>"
    return f"{tool_label} ({summary})" if summary else tool_label


def _build_approval_footer(details: list[tuple[str | None, str | None]]) -> str:
    """Build the end-of-stream auto-approval footer.

    Returns an empty string when no approvals fired (caller suppresses the
    footer entirely in that case). For longer lists, the first
    `FOOTER_MAX_ENTRIES` are shown verbatim and the rest collapse into
    `"…and N more"` so the footer can't blow up a small chat UI.
    """
    if not details:
        return ""
    shown = [_format_approval_entry(t, s) for t, s in details[:FOOTER_MAX_ENTRIES]]
    body = ", ".join(shown)
    if len(details) > FOOTER_MAX_ENTRIES:
        body = f"{body}, …and {len(details) - FOOTER_MAX_ENTRIES} more"
    return f"Auto-approved during this turn: {body}"


def _completion_state(*, saw_message_end: bool, cancelled: bool, errored: bool) -> str:
    if errored:
        return "error"
    if cancelled:
        return "cancelled"
    if saw_message_end:
        return "message_end"
    return "truncated"


class AskOllieResult(BaseModel):
    text: str
    thread_id: str
    navigate: list[str] = []
    complete: bool = True
    cancelled: bool = False


class _CometClientProto(Protocol):
    async def discover_pod(self, workspace: str) -> PodDiscovery: ...


class _OllieClientProto(Protocol):
    async def wait_ready(
        self, compute_url: str, ppauth: str, *, on_tick: OnTick | None = None
    ) -> None: ...

    async def create_session(
        self, compute_url: str, ppauth: str, workspace: str, body: dict[str, Any]
    ) -> str: ...

    def stream_events(
        self,
        compute_url: str,
        ppauth: str,
        workspace: str,
        session_id: str,
        *,
        last_event_id: int | None = None,
    ) -> AsyncIterator[SSEEvent]: ...

    async def confirm_session(
        self,
        compute_url: str,
        ppauth: str,
        workspace: str,
        session_id: str,
        *,
        tool_use_id: str,
        decision: str,
    ) -> None: ...


def _str_or_none(d: dict[str, Any], key: str) -> str | None:
    """Read a string field from a payload dict, returning None for any non-string."""
    value = d.get(key)
    return value if isinstance(value, str) else None


def _navigate_url(payload: dict[str, Any]) -> str | None:
    path = payload.get("path")
    if not isinstance(path, str):
        return None
    search = payload.get("search")
    if not isinstance(search, dict) or not search:
        return path

    # NavigatePayload.search carries TanStack Router structured values
    # (filter arrays, sort specs). We can't faithfully reconstruct those as a
    # query string here; flatten the scalar keys and warn on the rest.
    flat = {k: v for k, v in search.items() if isinstance(v, str | int | float | bool)}
    dropped = sorted(set(search) - set(flat))
    if dropped:
        logger.debug("ask_ollie.navigate dropped non-scalar search keys: %s", dropped)
    if flat:
        return f"{path}?{urlencode(flat)}"
    return path


async def run_ask_ollie(
    *,
    query: str,
    page_context: str | None = None,
    attach_resources: list[str] | None = None,
    thread_id: str | None = None,
    project_name: str | None = None,
    ctx: Context[ServerSession, None] | None = None,
    settings: Settings | None = None,
    comet_client: _CometClientProto | None = None,
    ollie_client: _OllieClientProto | None = None,
) -> AskOllieResult:
    """Orchestrate pod discovery → warmup → POST /sessions → SSE stream → final result."""
    t0 = time.monotonic()
    pod_warmup_ms = 0
    first_event_at: float | None = None
    events_seen = 0
    # (target_tool, summary) for each auto-approved pod tool call this turn.
    # Drives the end-of-stream footer + analytics event + session-complete log.
    # Tuples (not a set) so duplicate `(tool, summary)` pairs across a single
    # call are visible to the user — they reflect distinct pod actions even when
    # they happen to share a summary; dedup-by-tool_use_id upstream already
    # prevents the same pod confirm from being recorded twice.
    auto_approval_details: list[tuple[str | None, str | None]] = []
    errored = False
    saw_message_end = False
    cancelled = False
    # Initialize the error-emit locals so the finally clause can reference
    # them on the happy path without UnboundLocalError. The except arm
    # below assigns real values when ``errored`` flips to True.
    error_kind: str = "unknown"
    error_exception_type: str = ""
    error_cause_type: str = ""
    error_upstream_code: str | None = None

    try:
        settings = settings or get_settings()
        api_key, workspace = require_ollie_config(settings)

        if attach_resources:
            # Pending pod-side ChatRequest schema support — log at debug so callers
            # learn the parameter is currently a no-op without silent drops.
            logger.debug(
                "ask_ollie.attach_resources accepted but pod schema not yet wired: %s",
                attach_resources,
            )

        comet: _CometClientProto = comet_client or CometClient(
            base_url=settings.comet_url_override, api_key=api_key
        )
        ollie: _OllieClientProto = ollie_client or OllieClient(
            ready_timeout_s=settings.opik_mcp_pod_ready_timeout_s,
            ready_interval_s=settings.opik_mcp_pod_ready_interval_s,
        )

        if ctx is not None:
            await ctx.info("Discovering Ollie pod...")
        logger.info(
            "ask_ollie.discover workspace=%s base_url=%s",
            workspace,
            settings.comet_url_override,
        )
        discovery = await comet.discover_pod(workspace)

        if ctx is not None:
            await ctx.info(f"Pod ready check at {discovery.compute_url}")

        # Unified strictly-monotonic progress counter for the whole tool call
        # (warmup ticks + SSE events + heartbeat). MCP spec §Lifecycle/Timeouts
        # requires `progress` values to strictly increase across a single
        # progressToken; without a shared counter the warmup's elapsed-seconds
        # scale (e.g. 30.0) would collide with the SSE loop restarting at 1, 2,
        # 3 and the host would see a decrease. Integer-only on the wire so strict
        # hosts that expect `type: integer` accept every frame.
        progress_counter = 0

        async def on_tick(elapsed: float) -> None:
            nonlocal progress_counter
            if ctx is None:
                return
            progress_counter += 1
            await ctx.report_progress(
                progress=progress_counter,
                message=f"Pod warming ({elapsed:.0f}s)",
            )

        warmup_start = time.monotonic()
        await ollie.wait_ready(discovery.compute_url, discovery.ppauth, on_tick=on_tick)
        pod_warmup_ms = int((time.monotonic() - warmup_start) * 1000)

        if ctx is not None:
            await ctx.info("Pod ready. Creating session...")

        body: dict[str, Any] = {"message": query}
        if thread_id is not None:
            body["session_id"] = thread_id
        # Pod's PageContext (ollie-assist `types/chat.py` ChatRequest.context)
        # is the structured channel for project scoping. The wire field is
        # exactly `context` (ChatRequest declares `context: PageContext | None`);
        # Pydantic's default `extra="ignore"` silently drops unknown keys, so
        # any other name (e.g. `page_context`) would be a no-op. Ollie's read
        # tools resolve project name → id via `session.project_name` server-side
        # (see ollie-assist `tools/read/tool.py:145`). Note: there is NO
        # `<current_project>` block in Ollie's system prompt — the project
        # only surfaces through tool calls, so asking Ollie to introspect for
        # one is misleading; verify by exercising a project-scoped tool.
        # Ollie clears project state on every request whose body lacks
        # `context`, so callers must re-send this on every follow-up. Truthy
        # check rejects empty strings, which would be deserialized as a
        # valid-but-broken filter. Name-only matches the Opik Python/TS SDKs —
        # `project_id` is a read-side concept (filter on list endpoints), not
        # a write-path identifier.
        if project_name:
            body["context"] = {"project_name": project_name}
        if page_context is not None:
            # Free-form markdown of the user's current view; parallel to the
            # structured `context` envelope above.
            body["snapshot"] = page_context

        session_id = await ollie.create_session(
            discovery.compute_url, discovery.ppauth, workspace, body
        )
        logger.info("ask_ollie.session_created session_id=%s", session_id)

        if ctx is not None:
            await ctx.info(f"Streaming events for session {session_id}...")

        text_buffer = ""
        navigate: list[str] = []

        # Universal per-event progress + idle heartbeat keep host-side tool-call
        # timeouts alive when the pod is silent (long SDK calls, big tool I/O).
        # `progress_counter` (defined above, shared with the warmup phase) is the
        # strictly-monotonic progress value the MCP spec requires; both the
        # real-event path and the heartbeat path increment it.
        # No lock needed: asyncio's cooperative scheduling means only one task runs
        # between `await` points. INVARIANT (load-bearing for monotonicity): the
        # `progress_counter += 1` and the `await ctx.report_progress(...)` MUST
        # appear with no `await` between them in every code path that emits a
        # progress frame — otherwise an interleaved write could let a SMALLER value
        # be emitted after a LARGER one.
        heartbeat_interval = float(settings.opik_mcp_heartbeat_interval_s)
        stream_idle_timeout = float(settings.opik_mcp_stream_idle_timeout_s)
        now = time.monotonic()
        # `last_progress_at` tracks when *any* progress (event or heartbeat) was
        # emitted — drives the half-interval polling. `last_real_event_at` tracks
        # only real SSE events — drives the idle-timeout watchdog. Splitting them
        # is what lets the heartbeat keep the host alive without masking a dead pod.
        last_progress_at = now
        last_real_event_at = now
        # Dedupe `confirm_required` events by `tool_use_id` — a pod retry or stream
        # reconnect can re-emit the same event, and YOLO would otherwise send
        # `decision="yes"` twice (audit log shows two rows, but a non-idempotent
        # pod tool would execute the write twice). Set is scoped to this call so a
        # new session starts fresh.
        seen_tool_use_ids: set[str] = set()

        async def heartbeat_loop() -> None:
            nonlocal progress_counter, last_progress_at
            # A non-positive interval disables the heartbeat — both as an explicit
            # opt-out and as a defensive guard: `anyio.sleep(0)` in a tight loop
            # would starve the SSE consumer and flood the host with progress frames.
            if heartbeat_interval <= 0:
                return
            # Poll at half the heartbeat interval so a real event arriving just
            # after a tick check doesn't push the next heartbeat a full interval
            # past the deadline.
            while True:
                await anyio.sleep(heartbeat_interval / 2)
                now_ = time.monotonic()
                if stream_idle_timeout > 0 and now_ - last_real_event_at > stream_idle_timeout:
                    idle_for = now_ - last_real_event_at
                    logger.error(
                        "ask_ollie.stream_idle session_id=%s idle_for=%.1fs threshold=%.1fs",
                        session_id,
                        idle_for,
                        stream_idle_timeout,
                    )
                    # Raising from the heartbeat tears down the task group, which
                    # cancels the SSE consumer. The BaseExceptionGroup unwrap below
                    # filters the resulting CancelledError so callers see the
                    # OllieStreamError with the diagnostic message.
                    raise OllieStreamError(
                        f"Ollie pod stream idle for {idle_for:.0f}s "
                        f"(threshold {stream_idle_timeout:.0f}s); aborting."
                    )
                if ctx is None:
                    continue
                if now_ - last_progress_at >= heartbeat_interval:
                    progress_counter += 1
                    last_progress_at = now_
                    try:
                        await ctx.report_progress(progress=progress_counter, message="streaming")
                    except Exception:
                        # The session can drop mid-stream (host disconnect, network
                        # blip); a heartbeat failure shouldn't tear down the SSE
                        # loop — the next real event will retry on its own.
                        logger.debug(
                            "ask_ollie.heartbeat ctx.report_progress failed", exc_info=True
                        )

        try:
            async with anyio.create_task_group() as tg:
                if ctx is not None:
                    tg.start_soon(heartbeat_loop)
                try:
                    async for sse in ollie.stream_events(
                        discovery.compute_url, discovery.ppauth, workspace, session_id
                    ):
                        events_seen += 1
                        if first_event_at is None:
                            first_event_at = time.monotonic()
                        progress_counter += 1
                        now_event = time.monotonic()
                        last_real_event_at = now_event
                        last_progress_at = now_event
                        if ctx is not None:
                            await ctx.report_progress(progress=progress_counter, message=sse.event)

                        evt = sse.event
                        payload = sse.data.get("payload", {}) if isinstance(sse.data, dict) else {}
                        if not isinstance(payload, dict):
                            payload = {}

                        if evt in ("thinking_delta", "message_delta"):
                            chunk = payload.get("delta")
                            if isinstance(chunk, str):
                                text_buffer += chunk

                        elif evt in ("tool_call_start", "tool_call_end"):
                            display = payload.get("display") or payload.get("tool") or ""
                            if ctx is not None:
                                await ctx.info(f"{evt}: {display}" if display else evt)

                        elif evt in (
                            "compaction_start",
                            "compaction_end",
                            "compaction_delta",
                        ):
                            if ctx is not None:
                                await ctx.info(evt)

                        elif evt == "confirm_required":
                            tool_use_id = _str_or_none(payload, "tool_use_id")
                            if not tool_use_id:
                                logger.warning(
                                    "ask_ollie.confirm_required missing tool_use_id; "
                                    "cannot approve — stream may stall."
                                )
                                continue

                            # Dedup: stream reconnect or pod retry can re-emit the
                            # same confirm_required event. Without this, YOLO would
                            # POST `decision="yes"` twice — for non-idempotent pod
                            # tools (add_test_suite_item, score) that's a duplicate
                            # write. The audit row also fires twice, masking the
                            # bug from anyone reading the log.
                            if tool_use_id in seen_tool_use_ids:
                                logger.warning(
                                    "ask_ollie.confirm_required duplicate tool_use_id=%s; "
                                    "skipping second approval (already auto-approved).",
                                    tool_use_id,
                                )
                                continue
                            seen_tool_use_ids.add(tool_use_id)

                            target_tool = _str_or_none(payload, "tool_name")
                            summary = _str_or_none(payload, "summary")
                            raw_input = payload.get("input")
                            tool_input = raw_input if isinstance(raw_input, dict) else {}

                            # Opt-out path (OPIK_MCP_AUTO_APPROVE=disabled).
                            #
                            # Two sub-paths now:
                            #   1. Host supports MCP elicitation → ask the user
                            #      to approve THIS specific tool. ACCEPT falls
                            #      through to the normal audit+POST flow below;
                            #      DENY/CANCEL still raises OllieStreamError so
                            #      the pod stream terminates cleanly (the pod
                            #      otherwise hangs waiting for a confirm POST).
                            #   2. Host without elicitation → keep the legacy
                            #      hard-error path so behavior on dumb hosts is
                            #      unchanged.
                            #
                            # Either way we add tool_use_id to seen so a retry
                            # with the same id can't bypass the opt-out by
                            # being re-evaluated as a fresh request.
                            if settings.opik_mcp_auto_approve == "disabled":
                                requested = summary or target_tool or tool_use_id
                                approved_via_elicit = False
                                # Allowlist short-circuit. If the user previously
                                # accepted with `always_approve=True` for this
                                # target_tool, skip the elicit entirely — no
                                # second prompt for the same tool in this session.
                                if target_tool is not None and target_tool in _SESSION_ALLOWLIST:
                                    approved_via_elicit = True
                                    logger.info(
                                        "ask_ollie.confirm session_id=%s "
                                        "tool_use_id=%s tool=%s "
                                        "via=session_allowlist",
                                        session_id,
                                        tool_use_id,
                                        target_tool,
                                    )
                                elif ctx is not None:
                                    outcome = await confirm_with_user(
                                        ctx,
                                        prompt=_format_elicit_prompt(target_tool, summary),
                                        timeout_s=settings.opik_mcp_elicit_timeout_seconds,
                                        tool="ask_ollie",
                                        entity_type=target_tool or "tool_use",
                                        entity_id=tool_use_id,
                                    )
                                    approved_via_elicit = outcome.decision.approved
                                    if (
                                        approved_via_elicit
                                        and outcome.remember
                                        and target_tool is not None
                                    ):
                                        # First accept with the toggle on:
                                        # remember the tool name for the rest of
                                        # this MCP subprocess lifetime.
                                        _SESSION_ALLOWLIST.add(target_tool)
                                        logger.info(
                                            "ask_ollie.allowlist_added tool=%s",
                                            target_tool,
                                        )
                                if not approved_via_elicit:
                                    raise OllieStreamError(
                                        "Auto-approval disabled "
                                        "(OPIK_MCP_AUTO_APPROVE=disabled). "
                                        f"Ollie requested: {requested}. "
                                        "Re-run after deciding manually, or set "
                                        "OPIK_MCP_AUTO_APPROVE=enabled to allow this turn."
                                    )

                            # YOLO mode invariant: never send `decision="yes"` to the
                            # pod without an audit row landing first. The audit log
                            # is the only safety net under always-on auto-approval —
                            # see ADR 0005 §"Audit-then-POST ordering".
                            try:
                                audit.write_auto_approval(
                                    workspace=workspace,
                                    session_id=session_id,
                                    tool_use_id=tool_use_id,
                                    target_tool=target_tool,
                                    summary=summary,
                                    input=tool_input,
                                )
                            except Exception:
                                logger.error(
                                    "ask_ollie.audit_failed session_id=%s tool_use_id=%s "
                                    "tool=%s — confirm POST suppressed; pod stream may stall",
                                    session_id,
                                    tool_use_id,
                                    target_tool,
                                    exc_info=True,
                                )
                                continue
                            auto_approval_details.append((target_tool, summary))
                            logger.info(
                                "ask_ollie.confirm session_id=%s tool_use_id=%s tool=%s "
                                "decision=yes (yolo)",
                                session_id,
                                tool_use_id,
                                target_tool,
                            )
                            if ctx is not None:
                                await ctx.info(
                                    f"Ollie auto-approved: {summary or target_tool or tool_use_id}"
                                )
                            try:
                                await ollie.confirm_session(
                                    discovery.compute_url,
                                    discovery.ppauth,
                                    workspace,
                                    session_id,
                                    tool_use_id=tool_use_id,
                                    decision="yes",
                                )
                            except Exception as exc:
                                # Audit row is already written (intent recorded). A
                                # transient confirm POST failure leaves the pod
                                # stalled — surface a typed error instead of bubbling
                                # the raw httpx exception to the host LLM.
                                raise OllieStreamError(
                                    f"Ollie confirm POST failed for"
                                    f" tool_use_id={tool_use_id}: {exc}"
                                ) from exc

                        elif evt == "navigate":
                            url = _navigate_url(payload)
                            if url is not None:
                                navigate.append(url)

                        elif evt == "error":
                            raw_message = payload.get("message")
                            # Avoid leaking the raw payload dict to the host LLM (and
                            # downstream to the user) when the pod sends a malformed
                            # error event. A generic string is more honest than a
                            # serialized dict the model will try to interpret.
                            message = (
                                raw_message
                                if isinstance(raw_message, str) and raw_message
                                else "Unknown pod error"
                            )
                            # ``code`` is an optional structured field on the SSE
                            # error frame. Captured here (str only, length-capped
                            # at the bucketing layer) so analytics can group
                            # upstream failures without leaking the message.
                            raw_code = payload.get("code")
                            upstream_code = raw_code if isinstance(raw_code, str) else None
                            raise OllieStreamError(message, upstream_code=upstream_code)

                        elif evt == "message_end":
                            saw_message_end = True
                            break

                        elif evt == "message_cancelled":
                            # User-initiated cancellation: stream terminates cleanly
                            # but the response is partial. Don't set saw_message_end.
                            cancelled = True
                            if ctx is not None:
                                await ctx.warning("Generation cancelled — response is partial.")
                            break

                        else:
                            # Forward-compat: pod may add new event types. Don't
                            # crash, but leave a trace for protocol-drift debugging.
                            logger.debug("ask_ollie.unknown_event evt=%s", evt)
                finally:
                    tg.cancel_scope.cancel()
        except BaseExceptionGroup as eg:
            # anyio's task group wraps body exceptions in a BaseExceptionGroup even
            # when there is only one. Callers (and existing tests) expect the bare
            # OllieStreamError / etc., so unwrap to the underlying real exception.
            # Cancellation exceptions (`anyio.get_cancelled_exc_class()`) can leak
            # from the heartbeat task when the SSE body raises — filter them out
            # so they don't mask the user-facing error or block the unwrap.
            #
            # Plain ``raise real[0]`` (no ``from``) preserves the inner
            # ``__cause__`` chain — production ``OllieStreamError`` instances are
            # raised via ``raise OllieStreamError(...) from OpikAuthError(...)``
            # and the analytics emit relies on ``unwrap_to_real_cause`` walking
            # that chain. An earlier ``from None`` here clobbered ``__cause__``
            # and made every wrapped failure look like ``unknown`` in BI.
            cancelled_cls = anyio.get_cancelled_exc_class()
            real = [e for e in eg.exceptions if not isinstance(e, cancelled_cls)]
            if len(real) == 1 and isinstance(real[0], Exception):
                # ``raise real[0]`` (no ``from``) is intentional. ``from eg``
                # would overwrite the inner ``__cause__`` with the task group;
                # ``from None`` would drop the chain entirely. Neither is what
                # analytics need — see the block-level comment above.
                raise real[0]  # noqa: B904
            raise

        if not saw_message_end and not cancelled:
            logger.warning(
                "ask_ollie.stream_truncated session_id=%s text_len=%d",
                session_id,
                len(text_buffer),
            )
            if ctx is not None:
                await ctx.warning("Stream ended without message_end — response may be incomplete.")

        footer = _build_approval_footer(auto_approval_details)
        # Assemble the final text:
        # - approvals + content: content first, footer separated by blank line
        # - approvals + no content: footer alone (no "(no response)" prefix —
        #   we DID do something, just produced no message text)
        # - no approvals + no content: "(no response)" placeholder
        if text_buffer and footer:
            final_text = f"{text_buffer}\n\n{footer}"
        elif text_buffer:
            final_text = text_buffer
        elif footer:
            final_text = footer
        else:
            final_text = "(no response)"

        logger.info(
            "ask_ollie.session_complete session_id=%s completion=%s text_len=%d auto_approvals=%d",
            session_id,
            _completion_state(
                saw_message_end=saw_message_end, cancelled=cancelled, errored=errored
            ),
            len(text_buffer),
            len(auto_approval_details),
        )

        return AskOllieResult(
            text=final_text,
            thread_id=session_id,
            navigate=navigate,
            complete=saw_message_end,
            cancelled=cancelled,
        )
    except BaseException as exc:
        if isinstance(exc, anyio.get_cancelled_exc_class()):
            cancelled = True
        else:
            errored = True
            # Stash class + bucketed kind here so the finally block can
            # emit them without needing to read sys.exc_info(). Privacy:
            # class-name and class-keyed bucket only — exc.args is never
            # read at any emit site.
            error_exception_type = type(exc).__name__
            if isinstance(exc, Exception):
                error_kind = bucket_exception(exc)
                # Unwrap pure-envelope wrappers (OllieStreamError, ToolError)
                # so dashboards can split on the real upstream culprit. Only
                # populated when the unwrap actually finds a distinct cause
                # — a bare ``OllieStreamError`` with no chained cause leaves
                # ``cause_type`` empty and the event carries the wrapper only.
                real_cause = unwrap_to_real_cause(exc)
                if real_cause is not exc:
                    error_cause_type = type(real_cause).__name__
            else:
                error_kind = "unknown"
            # ``getattr`` returns ``Any`` — narrow to ``str`` here so the
            # finally block doesn't need to re-check the type and so a future
            # refactor that accidentally stores a non-string would be caught
            # by mypy on this assignment.
            upstream_code = getattr(exc, "upstream_code", None)
            error_upstream_code = upstream_code if isinstance(upstream_code, str) else None
        raise
    finally:
        ttfe_ms: int = int((first_event_at - t0) * 1000) if first_event_at is not None else -1
        props: dict[str, str] = {
            "success": "false" if errored else "true",
            "total_duration_ms": str(int((time.monotonic() - t0) * 1000)),
            "pod_warmup_ms": str(pod_warmup_ms),
            "time_to_first_event_ms": str(ttfe_ms),
            "event_count": str(events_seen),
            "had_continuation": str(thread_id is not None).lower(),
            "had_page_context": str(page_context is not None).lower(),
            "had_project_name": str(project_name is not None).lower(),
            "attach_resources_count": bucket_count(len(attach_resources or [])),
            "completion_state": _completion_state(
                saw_message_end=saw_message_end,
                cancelled=cancelled,
                errored=errored,
            ),
            "auto_approvals_count": str(len(auto_approval_details)),
            "auto_approval_tools": _bucket_auto_approval_tools(auto_approval_details),
        }
        # Stamp the bucketed session context (env cohort + MCP host) so BI can
        # segment ask_ollie usage on a single table — same block tool_called
        # carries. ctx (hence session) may be None when invoked outside a host.
        session = getattr(ctx, "session", None) if ctx is not None else None
        props.update(call_context_props(session))
        if errored:
            props["error_kind"] = error_kind
            props["exception_type"] = error_exception_type
            if error_cause_type:
                props["cause_type"] = error_cause_type
            if error_upstream_code:
                # Length-cap as a defense against a misbehaving pod that
                # stamps a long string into ``code``. 64 chars is enough
                # for every legitimate code we ship while staying well
                # under any plausible PII leak.
                props["upstream_error_code"] = error_upstream_code[:64]
        try:
            _analytics().track_event(EVENT_ASK_OLLIE_COMPLETED, props)
        except Exception:
            logger.debug("ask_ollie_completed emit failed", exc_info=True)
