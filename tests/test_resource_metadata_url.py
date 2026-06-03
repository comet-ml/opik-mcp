"""Unit tests for ``_resource_metadata_url``.

The URL advertised in ``WWW-Authenticate`` must match where the
protected-resource metadata route actually lives on the server. The route
is registered at the application root (``/.well-known/oauth-protected-resource``),
not nested under the resource path. So when the resource URI is
``http://127.0.0.1:8888/mcp``, the advertised metadata URL must be
``http://127.0.0.1:8888/.well-known/oauth-protected-resource`` and NOT
``http://127.0.0.1:8888/mcp/.well-known/oauth-protected-resource`` — the
latter falls through to the MCP path's auth middleware and 401s, silently
breaking the host's discovery chain.
"""

from opik_mcp.config import Settings
from opik_mcp.server import _resource_metadata_url


def _settings(resource_uri: str | None) -> Settings:
    return Settings(opik_mcp_resource_uri=resource_uri, _env_file=None)  # type: ignore[call-arg]


def test_resource_uri_with_path_strips_to_host_root() -> None:
    """Resource URI with a path → metadata URL uses scheme://authority only."""
    url = _resource_metadata_url(_settings("http://127.0.0.1:8888/mcp"))
    assert url == "http://127.0.0.1:8888/.well-known/oauth-protected-resource"


def test_resource_uri_already_at_host_root() -> None:
    """No path on the resource URI — same behavior."""
    url = _resource_metadata_url(_settings("http://127.0.0.1:8888"))
    assert url == "http://127.0.0.1:8888/.well-known/oauth-protected-resource"


def test_resource_uri_with_trailing_slash() -> None:
    url = _resource_metadata_url(_settings("http://127.0.0.1:8888/mcp/"))
    assert url == "http://127.0.0.1:8888/.well-known/oauth-protected-resource"


def test_https_authority_preserved() -> None:
    url = _resource_metadata_url(_settings("https://www.comet.com/api/v1/mcp"))
    assert url == "https://www.comet.com/.well-known/oauth-protected-resource"


def test_unconfigured_falls_back_to_relative_path() -> None:
    """No resource URI configured — fall back to the bare path. Hosts that
    resolve relative to the 401 URL will land on the right authority anyway.
    """
    url = _resource_metadata_url(_settings(None))
    assert url == "/.well-known/oauth-protected-resource"


def test_malformed_resource_uri_falls_back_to_relative_path() -> None:
    """Garbage in the resource URI → safe fallback, not a crash."""
    url = _resource_metadata_url(_settings("not-a-url"))
    assert url == "/.well-known/oauth-protected-resource"
