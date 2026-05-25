import stat
from pathlib import Path
from uuid import UUID

import pytest

from opik_mcp.analytics import identity
from opik_mcp.config import Settings


@pytest.fixture(autouse=True)
def _fresh_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    # Identity resolution reads OPIK_API_KEY / COMET_WORKSPACE off the env via
    # pydantic-settings — clear them so tests construct Settings deterministically
    # regardless of the developer shell. Each test re-sets what it actually needs.
    monkeypatch.delenv("OPIK_API_KEY", raising=False)
    monkeypatch.delenv("COMET_WORKSPACE", raising=False)
    monkeypatch.delenv("COMET_WORKSPACE_ID", raising=False)
    identity._get_install_id.cache_clear()
    return tmp_path


def test_install_id_created_on_first_call(_fresh_home: Path) -> None:
    val = identity.get_install_id()
    UUID(val)  # raises if not a UUID
    path = _fresh_home / ".opik-mcp" / "install-id"
    assert path.read_text().strip() == val


def test_install_id_persists_across_cache_clear(_fresh_home: Path) -> None:
    first = identity.get_install_id()
    identity._get_install_id.cache_clear()
    assert identity.get_install_id() == first


def test_install_id_file_is_mode_0600(_fresh_home: Path) -> None:
    identity.get_install_id()
    path = _fresh_home / ".opik-mcp" / "install-id"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_corrupt_file_is_regenerated(_fresh_home: Path) -> None:
    path = _fresh_home / ".opik-mcp" / "install-id"
    path.parent.mkdir(parents=True)
    path.write_text("not-a-uuid")
    val = identity.get_install_id()
    UUID(val)
    assert path.read_text().strip() == val


def test_resolve_anonymous_id_prefers_workspace(_fresh_home: Path) -> None:
    s = Settings(comet_workspace="ws-1")
    assert identity.resolve_anonymous_id(s) == "ws-1"


def test_resolve_anonymous_id_falls_back_to_install_id(_fresh_home: Path) -> None:
    s = Settings(comet_workspace=None)
    val = identity.resolve_anonymous_id(s)
    UUID(val)


# --- api_key_sha256 ------------------------------------------------------- #


def test_api_key_sha256_is_64char_lowercase_hex() -> None:
    out = identity.api_key_sha256("sk-secret-abc")
    assert len(out) == 64
    assert all(c in "0123456789abcdef" for c in out)


def test_api_key_sha256_matches_known_value() -> None:
    """Known-Answer Test: pin the exact hex for an ASCII input so a future
    encoding regression (latin-1, trim, casing) breaks this test before it
    silently breaks the backend JOIN against the auth table.
    """
    assert identity.api_key_sha256("test") == (
        "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    )


def test_api_key_sha256_is_deterministic() -> None:
    a = identity.api_key_sha256("sk-secret-abc")
    b = identity.api_key_sha256("sk-secret-abc")
    assert a == b


def test_api_key_sha256_differs_per_key() -> None:
    assert identity.api_key_sha256("a") != identity.api_key_sha256("b")


def test_api_key_sha256_is_not_its_own_preimage() -> None:
    """Defends against a future broken impl (identity function, no-op hash)
    that would return the input verbatim — silently logging plaintext keys.
    """
    raw = "sk-canary-DO-NOT-LEAK-12345"
    assert identity.api_key_sha256(raw) != raw


def test_api_key_sha256_handles_unicode() -> None:
    """Non-ASCII keys must hash via UTF-8 without raising."""
    out = identity.api_key_sha256("sécrët-€-🔑")
    assert len(out) == 64


# --- resolve_anonymous_id (top-level user_id) ---------------------------- #


def test_resolve_anonymous_id_treats_empty_api_key_as_unset(_fresh_home: Path) -> None:
    """OPIK_API_KEY='' MUST behave identically to unset — no hash, fall
    through to the next priority. Documents that ``if settings.opik_api_key``
    (the implementation check) is truthy-aware.
    """
    s = Settings(opik_api_key="", comet_workspace="ws-1")
    # user_id stays workspace name (api_key hash is event_properties-only, not user_id)
    assert identity.resolve_anonymous_id(s) == "ws-1"


# --- install_id_was_freshly_generated ----------------------------------- #


def test_freshly_generated_true_on_first_create(_fresh_home: Path) -> None:
    """A brand-new HOME with no install-id file → flag is True."""
    ident, was_new = identity._get_install_id()
    UUID(ident)  # uuid-shaped
    assert was_new is True
    assert identity.install_id_was_freshly_generated() is True


def test_freshly_generated_false_on_existing_file(_fresh_home: Path) -> None:
    """install-id file already exists → flag is False."""
    (_fresh_home / ".opik-mcp").mkdir()
    (_fresh_home / ".opik-mcp" / "install-id").write_text("11111111-2222-3333-4444-555555555555")
    identity._get_install_id.cache_clear()

    ident, was_new = identity._get_install_id()
    assert ident == "11111111-2222-3333-4444-555555555555"
    assert was_new is False
    assert identity.install_id_was_freshly_generated() is False


def test_freshly_generated_false_on_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unwritable HOME → fallback id, flag is False (not 'new')."""

    def _boom(*_a, **_kw):
        raise OSError("read-only fs")

    monkeypatch.setattr(identity.Path, "home", lambda: identity.Path("/nonexistent"))
    monkeypatch.setattr(identity.Path, "mkdir", _boom)
    identity._get_install_id.cache_clear()

    ident, was_new = identity._get_install_id()
    assert ident == identity._FALLBACK_INSTALL_ID
    assert was_new is False


def test_get_install_id_returns_string_only(_fresh_home: Path) -> None:
    """Public get_install_id() must still return a bare string (back-compat)."""
    result = identity.get_install_id()
    assert isinstance(result, str)
    assert len(result) == 36  # UUID4 hex with dashes
