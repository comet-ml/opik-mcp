"""AI Spend (cost intelligence): summaries, compositions, users, sessions and agents."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import quote

from opik_mcp.client.base import OpikClientBase, _drop_none
from opik_mcp.client.errors import OpikPermissionError, OpikValidationError
from opik_mcp.client.protocols import SpendItemKind

_PREFIX = "/v1/private/ai-spend"

# kind -> (route, query parameter that names the item)
_ITEM_ROUTES: dict[SpendItemKind, tuple[str, str]] = {
    "mcp_server": ("mcp-servers", "server"),
    "skill": ("skills", "skill"),
    "built_in_tool": ("built-in-tools", "tool"),
}


class SpendAdminRequiredError(OpikPermissionError):
    """Spend endpoints answered 403: the key is valid but not an org admin's."""


def _admin_message(entity_hint: str) -> str:
    """The 403 sentence for spend, in the shape ``errors.raise_for_status`` uses.

    It names no environment variable: the hosted HTTP transport forwards the
    caller's inbound OAuth bearer, where there is no ``OPIK_API_KEY`` to set,
    and a 403 can also mean the credential belongs to another workspace.
    """
    return (
        f"Permission denied for {entity_hint} (403). Spend data for this workspace "
        "needs an organization admin's credentials."
    )


@contextmanager
def _admin_only(entity_hint: str) -> Iterator[None]:
    try:
        yield
    except SpendAdminRequiredError:
        raise
    except OpikPermissionError as exc:
        raise SpendAdminRequiredError(_admin_message(entity_hint)) from exc


def _segment(value: str, what: str) -> str:
    """A path segment safe to send, and safe to echo back.

    httpx collapses dot-segments; the id is echoed into copy-paste OQL, and into
    the one-line ``[read: …]`` header, which a newline would split in two. So
    empty, dots-only, quoted and control-character ids are refused.
    """
    has_control = any(char < " " or char == "\x7f" for char in value)
    if not value or not value.strip(".") or '"' in value or has_control:
        raise OpikValidationError(f"{what} {value!r} is not a valid id.")
    return quote(value, safe="")


def _window(
    project_name: str,
    interval_start: str | None,
    interval_end: str | None,
    user_email: str | None = None,
) -> dict[str, Any]:
    return _drop_none(
        {
            "project_name": project_name,
            "interval_start": interval_start,
            "interval_end": interval_end,
            "user_email": user_email,
        }
    )


class AiSpendEndpoints(OpikClientBase):
    """``POST /v1/private/ai-spend/...``; a 403 becomes ``SpendAdminRequiredError``."""

    async def _spend_post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        entity_hint: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with _admin_only(entity_hint):
            return await self._post_json(
                f"{_PREFIX}{path}",
                json=body,
                params=_drop_none(params) if params else None,
                entity_hint=entity_hint,
            )

    async def get_spend_summary(
        self,
        *,
        project_name: str,
        interval_start: str,
        interval_end: str,
        user_email: str | None = None,
    ) -> dict[str, Any]:
        """``POST /ai-spend/summary``."""
        return await self._spend_post(
            "/summary",
            _window(project_name, interval_start, interval_end, user_email),
            entity_hint="AI Spend summary",
        )

    async def get_spend_composition(
        self,
        *,
        project_name: str,
        interval_start: str,
        interval_end: str,
        user_email: str | None = None,
    ) -> dict[str, Any]:
        """``POST /ai-spend/composition``."""
        return await self._spend_post(
            "/composition",
            _window(project_name, interval_start, interval_end, user_email),
            entity_hint="AI Spend composition",
        )

    async def get_spend_lane_breakdown(
        self,
        lane_key: str,
        *,
        project_name: str,
        interval_start: str,
        interval_end: str,
        user_email: str | None = None,
    ) -> dict[str, Any]:
        """``POST /ai-spend/composition/{lane_key}/breakdown``; 400 on an unknown lane."""
        return await self._spend_post(
            f"/composition/{_segment(lane_key, 'Lane')}/breakdown",
            _window(project_name, interval_start, interval_end, user_email),
            entity_hint=f"AI Spend breakdown for lane {lane_key!r}",
        )

    async def list_spend_users(
        self,
        *,
        project_name: str,
        interval_start: str,
        interval_end: str,
        user_email: str | None = None,
        page: int = 1,
        size: int = 10,
        name: str | None = None,
        sorting: str | None = None,
    ) -> dict[str, Any]:
        """``POST /ai-spend/users`` — ``{page,size,total,content,sortable_by}``.

        ``sorting`` is a JSON string such as ``[{"field":"total_tokens","direction":"DESC"}]``.
        """
        return await self._spend_post(
            "/users",
            _window(project_name, interval_start, interval_end, user_email),
            entity_hint="AI Spend users",
            params={"page": page, "size": size, "name": name, "sorting": sorting},
        )

    async def list_spend_item_users(
        self,
        kind: SpendItemKind,
        item: str,
        *,
        project_name: str,
        interval_start: str,
        interval_end: str,
        user_email: str | None = None,
    ) -> list[Any]:
        """Users of one MCP server, skill or built-in tool: a top-level array of rows."""
        routed = _ITEM_ROUTES.get(kind)
        if routed is None:
            raise OpikValidationError(f"{kind!r} is not a spend item kind.")
        route, param = routed
        hint = f"AI Spend users of {kind.replace('_', ' ')} {item!r}"
        with _admin_only(hint):
            return await self._post_json_list(
                f"{_PREFIX}/{route}/users",
                json=_window(project_name, interval_start, interval_end, user_email),
                params={param: item},
                entity_hint=hint,
            )

    async def list_spend_sessions(
        self,
        *,
        project_name: str,
        interval_start: str,
        interval_end: str,
        page: int = 1,
        size: int = 10,
        filters: str | None = None,
        sorting: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """``POST /ai-spend/sessions`` — ``{page,size,total,content,sortable_by,harnesses}``.

        Takes no ``user_email``: the backend answers 403 when the body has one.
        Narrow by user with ``filters`` (``[{"field":"user_email",...}]``).
        """
        return await self._spend_post(
            "/sessions",
            _window(project_name, interval_start, interval_end),
            entity_hint="AI Spend sessions",
            params={
                "page": page,
                "size": size,
                "filters": filters,
                "sorting": sorting,
                "search": search,
            },
        )

    async def get_spend_session_narrative(
        self,
        session_id: str,
        *,
        project_name: str,
        interval_start: str | None = None,
        interval_end: str | None = None,
    ) -> dict[str, Any]:
        """``POST /ai-spend/sessions/{session_id}/narrative``."""
        return await self._spend_post(
            f"/sessions/{_segment(session_id, 'Session')}/narrative",
            _window(project_name, interval_start, interval_end),
            entity_hint=f"AI Spend session {session_id!r}",
        )

    async def get_spend_agents(
        self,
        *,
        project_name: str,
        interval_start: str,
        interval_end: str,
        user_email: str | None = None,
    ) -> dict[str, Any]:
        """``POST /ai-spend/agents``."""
        return await self._spend_post(
            "/agents",
            _window(project_name, interval_start, interval_end, user_email),
            entity_hint="AI Spend agents",
        )
