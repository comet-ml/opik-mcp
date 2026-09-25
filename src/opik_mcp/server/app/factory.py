from __future__ import annotations

from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route
from starlette.types import ASGIApp

from opik_mcp.analytics import boot_props
from opik_mcp.analytics.environment import collect_environment_fingerprint
from opik_mcp.analytics.wrappers import install_tools_listed_emitter
from opik_mcp.config import get_settings
from opik_mcp.server.app.instance import mcp
from opik_mcp.server.app.lifespan import _make_composed_lifespan
from opik_mcp.server.app.session import (
    install_request_auth_rebinding,
    install_session_instructions,
)
from opik_mcp.server.http.endpoints import _liveness, _not_found_json, _readiness
from opik_mcp.server.http.middleware import AuthRejectionMiddleware, BearerAuthMiddleware
from opik_mcp.server.http.oauth import (
    _oauth_protected_resource,
    _proxy_to_as,
    _resource_metadata_url,
)
from opik_mcp.server.http.paths import _PROTECTED_RESOURCE_METADATA_PATH, _PROXIED_OAUTH_PATHS
from opik_mcp.skills_resources import install_skill_resources


def build_app() -> ASGIApp:
    install_tools_listed_emitter(mcp)
    install_session_instructions(mcp)
    install_request_auth_rebinding(mcp)
    install_skill_resources(mcp)
    settings = get_settings()
    # Serve the transport at the configured path so it matches the advertised
    # resource URI behind a non-rewriting path-prefix proxy. Read at app-build
    # time (streamable_http_app reads it then), so env overrides take effect.
    mcp.settings.streamable_http_path = settings.opik_mcp_http_path
    # DNS-rebinding/Host-Origin guard. Default allow-lists cover localhost only,
    # so hosted deployments must add their public host (and browser-client
    # origins). Applied here for the same read-at-build-time reason as above.
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=settings.opik_mcp_dns_rebinding_protection,
        allowed_hosts=settings.allowed_hosts_list,
        allowed_origins=settings.allowed_origins_list,
    )
    app = mcp.streamable_http_app()
    # Replace Starlette's default plain-text 404 — see ``_not_found_json``.
    app.router.default = _not_found_json
    app.router.routes.append(Route("/health", _liveness, methods=["GET"]))
    app.router.routes.append(Route("/health/ready", _readiness, methods=["GET"]))
    app.router.routes.append(
        Route(
            _PROTECTED_RESOURCE_METADATA_PATH,
            _oauth_protected_resource,
            methods=["GET"],
        )
    )
    # AS / OAuth-flow probe paths — proxy to the configured AS so
    # split-host deployments (local docker-compose, dev clusters where
    # opik-mcp and opik-backend bind to different addresses) work the
    # same as the production single-edge deploy. Proxying (not redirect)
    # because some SDKs refuse to follow cross-origin OAuth-discovery
    # redirects and silently break their bootstrap.
    for path in _PROXIED_OAUTH_PATHS:
        app.router.routes.append(Route(path, _proxy_to_as, methods=["GET", "POST"]))
    settings = get_settings()
    app.add_middleware(
        BearerAuthMiddleware,
        resource_metadata_url=_resource_metadata_url(settings),
    )

    # GAP#1: emit lifecycle events from the lifespan so the hosted --factory
    # entrypoint (which bypasses __main__.main()) is no longer dark. Capture the
    # session-manager lifespan BEFORE overwriting it. Compute the fingerprint
    # synchronously here (it shells out on macOS — must not run in the async
    # lifespan) and only when this process will actually emit: a main()-owned
    # boot skips the emit, so skip the cost too.
    will_emit = not boot_props.lifecycle_owned_by_main()
    fingerprint_props = collect_environment_fingerprint() if will_emit else {}
    inner_lifespan = app.router.lifespan_context
    app.router.lifespan_context = _make_composed_lifespan(
        inner_lifespan, settings, fingerprint_props
    )

    # Outermost wrapper: observe 401/421/403 and emit auth_rejected (GAP#3). Pure
    # ASGI so streaming SSE is never buffered; the "lifespan" scope passes through
    # to the Starlette app so the composed lifespan above still runs.
    return AuthRejectionMiddleware(app, settings=settings)
