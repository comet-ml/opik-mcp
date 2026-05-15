import stat
from pathlib import Path
from uuid import UUID

import pytest

from opik_mcp.analytics import identity
from opik_mcp.config import Settings


@pytest.fixture(autouse=True)
def _fresh_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
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
