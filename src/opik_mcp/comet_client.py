import http.cookies
from dataclasses import dataclass
from typing import ClassVar

import httpx

from opik_mcp.error_kinds import ErrorKind


class CometAuthError(RuntimeError):
    """Comet-backend rejected the API key (401)."""

    error_kind: ClassVar[ErrorKind] = "auth"
    http_status: ClassVar[int | None] = 401


class CometPermissionError(CometAuthError):
    """Comet-backend returned 403 — caller is authenticated but the workspace
    rejects the request. Subclass of ``CometAuthError`` to preserve existing
    ``except CometAuthError`` callers; the ``error_kind`` / ``http_status``
    ClassVars shadow the parent's so analytics still distinguish the two.
    """

    error_kind: ClassVar[ErrorKind] = "permission"
    http_status: ClassVar[int | None] = 403


class OllieNotEnabledError(RuntimeError):
    """Workspace does not have ollie-assist enabled.

    Bucketed as ``"unknown"`` — it's a user-config problem with no clean
    place in the upstream-failure taxonomy. The Sentry skip-list in
    ``analytics/wrappers.py`` covers it separately by class.
    """

    error_kind: ClassVar[ErrorKind] = "unknown"
    http_status: ClassVar[int | None] = None


class CometProtocolError(RuntimeError):
    """Comet-backend response was not in the expected shape — our own
    contract-drift signal, not an upstream HTTP failure."""

    error_kind: ClassVar[ErrorKind] = "unknown"
    http_status: ClassVar[int | None] = None


@dataclass(frozen=True)
class PodDiscovery:
    compute_url: str
    ppauth: str


class CometClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client

    async def discover_pod(self, workspace: str) -> PodDiscovery:
        url = f"{self._base_url}/api/opik/ollie/compute-api-key"
        headers = {
            "Authorization": self._api_key,
            "Comet-Workspace": workspace,
            "Accept": "application/json",
        }
        if self._client is None:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=headers)
        else:
            resp = await self._client.get(url, headers=headers)

        if resp.status_code == 401:
            raise CometAuthError("Comet rejected the request (401). Check OPIK_API_KEY.")
        if resp.status_code == 403:
            raise CometPermissionError(
                "Comet rejected the request (403). The API key is valid "
                "but lacks access to this workspace. Check COMET_WORKSPACE."
            )
        if resp.status_code == 400:
            preview = resp.text[:300].replace("\n", " ")
            raise CometProtocolError(
                f"Comet returned 400 Bad Request for workspace={workspace!r}. Body: {preview!r}"
            )
        resp.raise_for_status()

        ctype = resp.headers.get("content-type", "")
        if "application/json" not in ctype:
            preview = resp.text[:120].replace("\n", " ")
            raise CometProtocolError(
                f"{url} returned content-type={ctype!r} (expected JSON). "
                f"Body preview: {preview!r}. "
                "Is comet-backend PR #5555 deployed to this host?"
            )

        body = resp.json()
        compute_url = body.get("computeURL")
        enabled = body.get("enabled")
        if enabled is False:
            raise OllieNotEnabledError(f"Ollie is not enabled for workspace '{workspace}'.")
        if not isinstance(compute_url, str) or not compute_url:
            raise CometProtocolError("compute-api-key response missing 'computeURL'.")

        ppauth = _extract_ppauth(resp.headers.get_list("set-cookie"))
        if not ppauth:
            raise CometProtocolError("compute-api-key response missing PPAUTH Set-Cookie.")

        return PodDiscovery(compute_url=_normalize_compute_url(compute_url), ppauth=ppauth)


_COMPUTE_URL_SUFFIX = "/api/get-python-panel-url"


def _normalize_compute_url(compute_url: str) -> str:
    trimmed = compute_url.rstrip("/")
    if trimmed.endswith(_COMPUTE_URL_SUFFIX):
        trimmed = trimmed[: -len(_COMPUTE_URL_SUFFIX)]
    return trimmed


def _extract_ppauth(set_cookies: list[str]) -> str | None:
    for raw in set_cookies:
        jar: http.cookies.SimpleCookie = http.cookies.SimpleCookie()
        jar.load(raw)
        if "PPAUTH" in jar:
            return jar["PPAUTH"].value
    return None
