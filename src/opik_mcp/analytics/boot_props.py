"""Boot-time analytics properties derived from ``Settings``.

Single source of truth for the auth/transport properties stamped on
``server_started`` / ``startup_error`` / ``auth_rejected``, called identically
from ``__main__.main()`` (``lifecycle_source="main"``) and the ``build_app()``
lifespan (``lifecycle_source="lifespan"``) so the two launch paths can never
drift in what they emit.

PRIVACY: every value returned here is a bool-string or an allowlisted enum
(``InstallationType`` / ``AuthMode`` / ``ResourceUriScheme`` in ``events.py``).
Raw URLs, hosts, and keys never appear in a return value — only their derived
class. CRITICAL: ``installation_type`` is "self-hosted" (hyphen), never
"self_hosted"; BI filters key off the exact string.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from opik_mcp.config import Settings

# Lifecycle sentinel env-var name. An env var (not a module-level bool) so that
# uvicorn ``--reload`` workers — spawned via multiprocessing — inherit it from
# ``os.environ`` and correctly skip their own lifespan emit. Set by
# ``__main__.main()``; read by the ``build_app()`` lifespan.
LIFECYCLE_SENTINEL = "_OPIK_MCP_LIFECYCLE_OWNED_BY_MAIN"


def mark_lifecycle_owned_by_main() -> None:
    """Record that ``__main__.main()`` owns lifecycle emits.

    The ``build_app()`` lifespan checks this and skips its own
    server_started/shutdown emit so a boot is never double-counted. NOT
    auto-reverted; tests clear it via the conftest autouse fixture.
    """
    os.environ[LIFECYCLE_SENTINEL] = "1"


def lifecycle_owned_by_main() -> bool:
    """True when ``main()`` has claimed lifecycle-emit ownership. Read by the
    ``build_app()`` lifespan to decide whether to emit."""
    return os.environ.get(LIFECYCLE_SENTINEL) == "1"

# Derived from the pydantic schema default at import time — NOT a hand-copied
# string literal. Immune to env overrides (reads the declared default, not a
# live ``Settings`` instance) and self-corrects if ``config.py`` changes it.
_DEFAULT_ALLOWED_HOSTS: str = Settings.model_fields["opik_mcp_allowed_hosts"].default
# Fail loudly at import if the field is ever switched to a ``default_factory``:
# ``.default`` would then be ``PydanticUndefined`` and the ``str`` annotation
# would lie, crashing every ``allowed_hosts_is_default()`` call instead.
assert isinstance(_DEFAULT_ALLOWED_HOSTS, str)


def _normalise_hosts(raw: str) -> str:
    """Strip whitespace around comma-separated host entries for comparison."""
    return ",".join(h.strip() for h in raw.split(","))


def installation_type(settings: Settings) -> str:
    """``"cloud"`` / ``"self-hosted"`` / ``"local"``. Delegates to error_tracking.

    Imported lazily so loading this module doesn't eagerly pull in
    ``error_tracking`` (and ``sentry_sdk``) — boot_props is reached on the
    per-event path in ``client._build_event``. It also keeps import order robust:
    ``error_tracking`` imports ``analytics.identity``, so a lazy import sidesteps
    any ordering fragility while the analytics package is initialising. The
    module is cached after first import, so the per-call cost is a dict lookup.
    """
    from opik_mcp.error_tracking import _installation_type

    return _installation_type(settings)


def resource_uri_scheme(settings: Settings) -> str:
    """Scheme of ``OPIK_MCP_RESOURCE_URI``: "https", "http", or "none".

    Never emits the raw URI. A scheme-less value (bare host) is assumed to be a
    public ingress and reports "https"; anything other than http(s) reports
    "none" so a misconfigured value is visible rather than mislabelled.
    """
    uri = settings.opik_mcp_resource_uri or ""
    if not uri:
        return "none"
    parsed = urlparse(uri if "://" in uri else f"https://{uri}")
    return parsed.scheme if parsed.scheme in ("https", "http") else "none"


def oauth_configured(settings: Settings) -> str:
    """bool-string: is an OAuth Authorization Server configured?"""
    return str(bool(settings.opik_mcp_as_url)).lower()


def dns_rebinding_protection(settings: Settings) -> str:
    """bool-string: is the Host/Origin DNS-rebinding guard enabled?"""
    return str(settings.opik_mcp_dns_rebinding_protection).lower()


def allowed_hosts_is_default(settings: Settings) -> str:
    """bool-string: is the Host allowlist still the shipped localhost default?

    Catches hosted deployments that forgot to widen the allowlist (which would
    421 every public request). Whitespace-insensitive so a cosmetically
    reformatted default still counts as default, not deliberate hardening.
    """
    live = _normalise_hosts(settings.opik_mcp_allowed_hosts)
    return str(live == _normalise_hosts(_DEFAULT_ALLOWED_HOSTS)).lower()


def auth_mode_at_boot(settings: Settings) -> str:
    """Settings-derived auth mode for boot events: "api_key" / "oauth" / "none".

    A BOOT/process-level signal, NOT the per-request mode. It reflects what an
    outbound Opik call would use absent an inbound bearer: an explicit
    ``OPIK_API_KEY`` (the static credential) wins; else a configured AS
    ("oauth"); else "none". An HTTP+OAuth process may have BOTH a static key and
    an AS configured — this then reports "api_key" while ``oauth_configured``
    reports "true", so BI should read the two together to spot hybrid deploys.
    The real per-request credential is resolved in
    ``opik_client.resolve_opik_config`` (inbound bearer wins) and surfaced as the
    per-request ``auth_mode`` in ``client._build_event``.
    """
    if settings.opik_api_key:
        return "api_key"
    if settings.opik_mcp_as_url:
        return "oauth"
    return "none"


def oauth_configured_from_env() -> str:
    """bool-string fallback for the config-fail path where ``Settings`` could
    not be constructed (so we read the raw env var directly)."""
    return str(bool(os.getenv("OPIK_MCP_AS_URL"))).lower()


def collect_boot_props(settings: Settings) -> dict[str, str]:
    """Settings-derived boot properties for lifecycle events. Safe to spread
    into an event's ``properties`` dict alongside the has_* / fingerprint props.

    Deliberately omits ``installation_type``: that is stamped on EVERY event by
    ``client._build_event``'s common block (the single source of truth), so
    duplicating it here would be dead — common always wins the merge.
    """
    return {
        "oauth_configured": oauth_configured(settings),
        "resource_uri_scheme": resource_uri_scheme(settings),
        "dns_rebinding_protection": dns_rebinding_protection(settings),
        "allowed_hosts_is_default": allowed_hosts_is_default(settings),
        "auth_mode": auth_mode_at_boot(settings),
    }


def server_started_props(
    settings: Settings,
    *,
    fingerprint_props: dict[str, str],
    lifecycle_source: str,
) -> dict[str, str]:
    """The full ``opik_mcp_server_started`` property dict.

    Single source of truth shared by ``__main__.main()`` (lifecycle_source=main)
    and the ``build_app()`` lifespan (lifecycle_source=lifespan) so the two boot
    paths can never drift. ``fingerprint_props`` is passed in because the caller
    controls WHEN ``collect_environment_fingerprint`` runs (it shells out on
    macOS and is timed against the lifespan anchor).
    """
    from opik_mcp.analytics.identity import install_id_was_freshly_generated

    return {
        "transport": settings.opik_mcp_transport.lower(),
        "analytics_enabled": str(settings.opik_mcp_analytics_enabled).lower(),
        "has_workspace": str(settings.comet_workspace is not None).lower(),
        "has_api_key": str(settings.opik_api_key is not None).lower(),
        "has_default_project": str(settings.opik_default_project_name is not None).lower(),
        "install_id_freshly_generated": str(install_id_was_freshly_generated()).lower(),
        "lifecycle_source": lifecycle_source,
        **collect_boot_props(settings),
        **fingerprint_props,
    }


def server_shutdown_props(
    *,
    reason: str,
    elapsed_seconds: float,
    lifecycle_source: str,
) -> dict[str, str]:
    """The full ``opik_mcp_server_shutdown`` property dict (shared by main() and
    the build_app() lifespan, same anti-drift rationale as server_started_props)."""
    from opik_mcp.analytics import transport_probe
    from opik_mcp.analytics.events import bucket_seconds

    return {
        "reason": reason,
        "lifespan_seconds_bucket": bucket_seconds(elapsed_seconds),
        "first_rpc_received": str(transport_probe.first_rpc_received()).lower(),
        "session_reached": str(transport_probe.session_reached()).lower(),
        "lifecycle_source": lifecycle_source,
    }
