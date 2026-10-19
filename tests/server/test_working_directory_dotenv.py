from __future__ import annotations

from pathlib import Path

import pytest

from opik_mcp.config import Settings
from opik_mcp.server.app.instance import build_server


def test_a_non_utf8_dotenv_in_the_working_directory_does_not_stop_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hosts start the server in the user's project, where a `.env` may be cp1252."""
    (tmp_path / ".env").write_bytes(b"X=\x97\n")
    monkeypatch.chdir(tmp_path)

    server = build_server(Settings())

    assert server.name == "opik-mcp"
