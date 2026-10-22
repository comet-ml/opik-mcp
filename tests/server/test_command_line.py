"""``opik-mcp --help`` and ``--version`` answer and exit; other arguments still serve."""

from __future__ import annotations

import io
import logging
import sys

import httpx
import pytest
import respx

from opik_mcp import __main__ as main_mod
from opik_mcp import command_line, error_tracking
from opik_mcp.analytics.identity import OPIK_MCP_VERSION

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
        [
            "opik-mcp",
            "--apiKey",
            "sk-canary-spaced",
            "--api-url=https://canary.example/api",
            "-ksk-canary-glued",
        ],
    )

    with caplog.at_level(logging.WARNING, logger="opik_mcp"):
        main_mod.main()

    assert "--apiKey (use OPIK_API_KEY)" in caplog.text
    assert "--api-url (use OPIK_URL)" in caplog.text
    assert "sk-canary" not in caplog.text
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

    warnings = [
        record.getMessage() for record in caplog.records if record.name.startswith("opik_mcp")
    ]
    assert warnings == []
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


@pytest.mark.parametrize(
    ("env", "warned"),
    [
        ({"COMET_URL_OVERRIDE": "http://localhost:5173"}, True),
        (
            {
                "COMET_URL_OVERRIDE": "http://localhost:5173",
                "OPIK_URL": "http://localhost:5173/api",
            },
            False,
        ),
        ({"COMET_URL_OVERRIDE": "https://comet.example.com"}, False),
    ],
)
def test_a_local_opik_set_up_as_a_comet_platform__is_told_to_set_opik_url(
    env: dict[str, str],
    warned: bool,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    served: _StubMcp,
) -> None:
    """Every call would go to /opik/api, which open-source Opik does not serve."""
    monkeypatch.delenv("OPIK_URL", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["opik-mcp"])

    with caplog.at_level(logging.WARNING, logger="opik_mcp"):
        main_mod.main()

    assert ("Set OPIK_URL=http://localhost:5173/api instead" in caplog.text) is warned


_CHECKED = "http://opik.test/api"


def _check(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answer: httpx.Response | Exception,
    base: str = _CHECKED,
    **env: str,
) -> tuple[object, str]:
    """`opik-mcp --check` with ``env``, the backend at ``base`` answering ``answer``."""
    for name in ("OPIK_API_KEY", "COMET_URL_OVERRIDE", "OPIK_WORKSPACE", "COMET_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPIK_URL", base)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["opik-mcp", "--check"])
    # A check is not a server session.
    monkeypatch.setattr(main_mod, "track_event", _refuse)
    monkeypatch.setattr(error_tracking, "setup_sentry", _refuse)
    with respx.mock(base_url=base, assert_all_called=False) as mock:
        route = mock.get("/v1/private/projects")
        if isinstance(answer, Exception):
            route.mock(side_effect=answer)
        else:
            route.mock(return_value=answer)
        with pytest.raises(SystemExit) as exited:
            main_mod.main()
    return exited.value.code, capsys.readouterr().out


@pytest.mark.parametrize(
    ("answer", "status", "said"),
    [
        (
            httpx.Response(200, json={"content": [], "total": 3}),
            0,
            f"OK: Opik at {_CHECKED}, workspace default, 3 projects visible.",
        ),
        (httpx.Response(401), 1, "Opik rejected the credential for projects (401). Check"),
        # A Comet-platform address on open-source Opik: a 404, or its web page.
        (httpx.Response(404), 1, f"No Opik API at {_CHECKED}. Open-source Opik serves"),
        (httpx.Response(200, text="<html></html>"), 1, f"No Opik API at {_CHECKED}."),
        (
            httpx.ConnectError("All connection attempts failed"),
            1,
            f"Could not reach Opik to list projects: All connection attempts failed "
            f"(tried {_CHECKED})",
        ),
    ],
)
def test_check__one_line_and_an_exit_status_never_the_key(
    answer: httpx.Response | Exception,
    status: int,
    said: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out = _check(monkeypatch, capsys, answer, OPIK_API_KEY="sk-canary-key")

    assert code == status
    assert said in out
    assert out.count("\n") == 1
    assert "sk-canary-key" not in out


def test_check__a_setting_that_fails_validation_is_named(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _check(monkeypatch, capsys, httpx.Response(200), COMET_WORKSPACE_ID="not-a-uuid")

    assert code == 1
    assert out == "An opik-mcp setting is invalid: COMET_WORKSPACE_ID.\n"


def test_check__an_unset_workspace_on_a_comet_platform_is_named(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """It is the account's default there, which may not be the one meant."""
    code, out = _check(
        monkeypatch,
        capsys,
        httpx.Response(200, json={"content": [], "total": 3}),
        base="https://comet.example.com/opik/api",
    )

    assert code == 0
    assert "workspace default (OPIK_WORKSPACE not set, so the account default)" in out
