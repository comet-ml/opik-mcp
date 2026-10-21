"""No test run may emit telemetry — pinned, not assumed.

Both channels are opt-out by DEFAULT-ON: `opik_mcp_analytics_enabled` and
`opik_mcp_sentry_enabled` are both `True` in `Settings`, which is correct for a
user's install and wrong for every CI run and every developer running `pytest`.
BI funnels would carry synthetic traffic and the Sentry project would carry
crashes that tests cause on purpose.

`tests/conftest.py` turns both off for the whole test process. That is one line
each, easy to drop in a refactor, and the failure is silent — nothing breaks, the
data just quietly gets worse. This file is the alarm.

It asserts the ENVIRONMENT rather than the loaded `Settings`, because the
environment is what a subprocess inherits, and the subprocess is the case the
in-process guards do not cover.
"""

from __future__ import annotations

import os

import pytest

from opik_mcp.config import Settings

#: Every switch that must be off during a test run, and what leaks without it.
TELEMETRY_SWITCHES = {
    "OPIK_MCP_ANALYTICS_ENABLED": "events would POST to stats.comet.com and pollute the BI funnel",
    "OPIK_MCP_SENTRY_ENABLED": "deliberate test crashes would land in the real Sentry project",
}


@pytest.mark.parametrize(("env_var", "consequence"), list(TELEMETRY_SWITCHES.items()))
def test_the_switch_is_off_in_the_environment(env_var: str, consequence: str) -> None:
    value = os.environ.get(env_var)
    assert value is not None, (
        f"{env_var} is not set for the test process — see tests/conftest.py. "
        f"Without it, {consequence}."
    )
    assert value.lower() in ("false", "0", "no"), (
        f"{env_var}={value!r} during a test run. {consequence.capitalize()}."
    )


@pytest.mark.parametrize("env_var", list(TELEMETRY_SWITCHES))
def test_settings_actually_read_the_switch(env_var: str) -> None:
    """The env var must still be the one `Settings` reads.

    Renaming a settings field without renaming it here would leave the test
    above passing on a variable nothing consumes — a green alarm wired to
    nothing, which is worse than no alarm.
    """
    field = env_var.lower()
    assert field in Settings.model_fields, (
        f"{env_var} no longer maps to a Settings field; this guard is checking a dead variable"
    )


def test_the_defaults_are_still_on_so_the_guard_is_doing_work() -> None:
    """If both channels ever default to off, this whole file is theatre — and
    the conftest lines it guards are dead weight. Fail so someone deletes them
    deliberately rather than leaving a test that cannot fail."""
    defaults = Settings.model_construct()
    assert defaults.opik_mcp_analytics_enabled or defaults.opik_mcp_sentry_enabled, (
        "both telemetry channels now default to off — this guard no longer proves anything"
    )
