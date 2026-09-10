"""The dispatcher runs stages; operations know their own specifics.

This is the boundary the write surface is built on, and it is the kind that
erodes one convenient branch at a time. So it is pinned here rather than left
to review: an operation's wire translation, its pre-flight resolves, the
backend answers it reinterprets and what it says about its result all live in
``writes/operations/``, reached through the hooks on its registry entry.
"""

from __future__ import annotations

import inspect

import pytest

from opik_mcp.writes import dispatch
from opik_mcp.writes.registry import WRITE_OPERATIONS, WRITE_REGISTRY


def test_the_dispatcher_names_no_operation() -> None:
    """A branch on an operation's name is the tell. The names are long and
    distinctive, so a plain source search finds them wherever they hide."""
    source = inspect.getsource(dispatch)
    named = sorted(name for name in WRITE_OPERATIONS if f'"{name}"' in source)
    assert named == [], (
        f"dispatch.py names {named}. Per-operation behaviour belongs in "
        "writes/operations/, wired through the hooks on the registry entry "
        "(build_fn, prepare_fn, retry_fn, decorate_fn, dry_run_note_fn, validate_fn)."
    )


def test_every_operation_says_how_to_build_its_request() -> None:
    """The default builder is the endpoint plus a dump of one item, which is
    right only for the plain creates. Anything with a batch envelope, a path
    id or a renamed field needs its own, and forgetting one would silently
    send the wrong body rather than fail."""
    plain = {"trace.create", "span.create"}
    for name, op in WRITE_REGISTRY.items():
        if name in plain:
            continue
        assert op.build_fn is not None, f"{name} would fall back to the default body"


@pytest.mark.parametrize("name", sorted(WRITE_OPERATIONS))
def test_every_hook_comes_from_an_operations_module(name: str) -> None:
    """A hook defined anywhere else would be the same coupling under a new
    name."""
    op = WRITE_REGISTRY[name]
    hooks = (op.build_fn, op.prepare_fn, op.retry_fn, op.decorate_fn, op.dry_run_note_fn)
    for hook in hooks:
        if hook is None:
            continue
        module = getattr(hook, "__module__", "")
        assert module.startswith("opik_mcp.writes.operations."), (
            f"{name} takes a hook from {module!r}"
        )
