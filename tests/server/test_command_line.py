"""``opik-mcp --help`` and ``--version`` answer and exit; other arguments still serve."""

from __future__ import annotations

import io
import logging
import sys
from collections.abc import Iterator

import pytest

from opik_mcp import __main__ as main_mod
from opik_mcp import command_line
from opik_mcp.analytics.identity import OPIK_MCP_VERSION
from opik_mcp.config import get_settings

HOSTED_URL = "https://www.comet.com/opik/api/v1/mcp"


class _StubMcp:
    def __init__(self) -> None:
        self.transports: list[str] = []

    def run(self, *, transport: str) -> None:
        self.transports.append(transport)


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def _refuse(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("--help and --version must answer before settings and analytics")


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> _StubMcp:
    stub = _StubMcp()
    monkeypatch.setattr("opik_mcp.server.mcp", stub)
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "stdio")
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    return stub


@pytest.mark.parametrize("argument", ["--help", "-h"])
def test_help_prints_the_install_guide_and_starts_nothing(
    argument: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    served: _StubMcp,
) -> None:
    monkeypatch.setattr(sys, "argv", ["opik-mcp", argument])
    monkeypatch.setattr(main_mod, "get_settings", _refuse)
    monkeypatch.setattr(main_mod, "track_event", _refuse)

    main_mod.main()

    printed = capsys.readouterr().out
    assert HOSTED_URL in printed
    assert "OPIK_URL=http://localhost:5173/api" in printed
    assert served.transports == []


@pytest.mark.parametrize("argument", ["--version", "-V"])
def test_version_prints_the_package_version_and_starts_nothing(
    argument: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    served: _StubMcp,
) -> None:
    monkeypatch.setattr(sys, "argv", ["opik-mcp", argument])
    monkeypatch.setattr(main_mod, "get_settings", _refuse)

    main_mod.main()

    assert capsys.readouterr().out == f"opik-mcp {OPIK_MCP_VERSION}\n"
    assert served.transports == []


def test_a_setting_that_fails_validation_does_not_block_help(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], served: _StubMcp
) -> None:
    monkeypatch.setenv("COMET_WORKSPACE_ID", "not-a-uuid")
    monkeypatch.setattr(sys, "argv", ["opik-mcp", "--help"])

    main_mod.main()

    assert HOSTED_URL in capsys.readouterr().out


def test_a_typescript_flag_is_named_with_its_env_var_and_its_value_is_not_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, served: _StubMcp
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["opik-mcp", "--apiKey", "sk-canary-value", "--api-url=https://canary.example/api"],
    )

    with caplog.at_level(logging.WARNING, logger="opik_mcp"):
        main_mod.main()

    assert "--apiKey (use OPIK_API_KEY)" in caplog.text
    assert "--api-url (use OPIK_URL)" in caplog.text
    assert "sk-canary-value" not in caplog.text
    assert "canary.example" not in caplog.text
    assert served.transports == ["stdio"]


def test_a_person_at_a_terminal_is_pointed_at_help(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, served: _StubMcp
) -> None:
    monkeypatch.setattr(sys, "argv", ["opik-mcp"])
    monkeypatch.setattr(sys, "stdin", _Terminal())

    with caplog.at_level(logging.WARNING, logger="opik_mcp"):
        main_mod.main()

    assert command_line.TERMINAL_HINT in caplog.messages
    assert served.transports == ["stdio"]


def test_a_host_on_a_pipe_starts_without_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, served: _StubMcp
) -> None:
    monkeypatch.setattr(sys, "argv", ["opik-mcp"])

    with caplog.at_level(logging.WARNING, logger="opik_mcp"):
        main_mod.main()

    assert command_line.TERMINAL_HINT not in caplog.messages
    assert served.transports == ["stdio"]


def test_the_http_server_at_a_terminal_gets_no_hint(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    started: list[str] = []
    monkeypatch.setattr(main_mod, "_run_transport", lambda _s, transport: started.append(transport))
    monkeypatch.setenv("OPIK_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setattr(sys, "argv", ["opik-mcp"])
    monkeypatch.setattr(sys, "stdin", _Terminal())

    with caplog.at_level(logging.WARNING, logger="opik_mcp"):
        main_mod.main()

    assert started == ["streamable-http"]
    assert command_line.TERMINAL_HINT not in caplog.messages
