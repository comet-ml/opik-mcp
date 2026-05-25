"""Process-global handshake-progress flags consumed by server_shutdown emit."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from opik_mcp.analytics import transport_probe


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    transport_probe.reset_for_tests()
    yield
    transport_probe.reset_for_tests()


def test_flags_default_false() -> None:
    assert transport_probe.first_rpc_received() is False
    assert transport_probe.session_reached() is False


def test_mark_first_rpc_flips_only_first_rpc() -> None:
    transport_probe.mark_first_rpc()
    assert transport_probe.first_rpc_received() is True
    assert transport_probe.session_reached() is False


def test_mark_session_reached_flips_only_session_reached() -> None:
    transport_probe.mark_session_reached()
    assert transport_probe.first_rpc_received() is False
    assert transport_probe.session_reached() is True


def test_marks_are_idempotent() -> None:
    transport_probe.mark_first_rpc()
    transport_probe.mark_first_rpc()
    assert transport_probe.first_rpc_received() is True


def test_reset_clears_both_flags() -> None:
    transport_probe.mark_first_rpc()
    transport_probe.mark_session_reached()
    transport_probe.reset_for_tests()
    assert transport_probe.first_rpc_received() is False
    assert transport_probe.session_reached() is False
