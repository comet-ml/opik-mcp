"""The shape every Opik link this server emits must have.

The Opik UI is project-scoped: a page is addressed by workspace *and* project,
and the paths the UI retired are still reachable through a compatibility shim
that fills the missing project slot from whatever the reader last had open. A
link built in the old shape therefore does not 404 — it opens somewhere
plausible and wrong, which is why this needs a test and not just review.

Two nets, because there are two ways to get it wrong:

- :func:`live_project_url` is the predicate every other link test asserts
  through, and the tests here are its own — they pin it against the exact
  shapes this repository has shipped by mistake.
- :func:`test_urls_are_built_only_where_the_area_set_is_enforced` stops the
  second failure mode: a handler that skips the builder and writes the path
  itself, which is how the experiment compare link drifted.
"""

from __future__ import annotations

import ast
import pathlib
import re
from typing import get_args

import pytest

from opik_mcp.read_list.ui_links import ProjectArea

_LIVE_AREAS = frozenset(get_args(ProjectArea))

#: ``…/<workspace>/projects/<project id>/<area>`` — the one shape v2 serves.
_PROJECT_SCOPED = re.compile(r"^https?://[^\s?#]*?/projects/[^/?#]+/(?P<area>[^?#]+)")

#: The backend's own redirect, which resolves workspace and project server-side.
_SESSION_REDIRECT = re.compile(r"^https?://[^\s?#]*?/v1/session/redirect/")

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "opik_mcp"

#: The one module allowed to write a UI path. Everything else calls it.
_URL_BUILDER = _SRC / "read_list" / "ui_links.py"

#: A path segment that names a UI area. On its own this says little — the REST
#: client spells most of these too — so :func:`_is_ui_path_fragment` qualifies it.
_UI_PATH_HINT = re.compile(r"/(projects|experiments|datasets|prompts|traces|optimizations)/")


#: A bare separator fragment — what an f-string leaves behind between two
#: slots when a path is assembled: ``f"{base}/{ws}/projects/{pid}/{area}"``.
_BARE_AREA = re.compile(r"^/(projects|experiments|datasets|prompts|traces|optimizations)/$")


def _is_ui_path_fragment(value: str) -> bool:
    """Does this literal look like part of a UI address being assembled?

    Assembly is what leaves the tell. A UI path built by hand reaches the AST
    as the pieces between the slots of an f-string, and the piece carrying the
    area is a bare ``/projects/`` — nothing before it, nothing after. That is
    what separates it from the three things that legitimately name the same
    words: REST routes with further segments (``/items/experiments/items``),
    the regexes in ``uri.py`` that parse a pasted link rather than build one,
    and prose in a description.

    A whole URL written as one literal is the other way to do it, so an
    ``http`` literal naming an area counts too — excluding ``/v1/``, which
    every backend route has and no UI address does.
    """
    if _BARE_AREA.fullmatch(value):
        return True
    if not value.startswith("http") or "/v1/" in value:
        return False
    return bool(_UI_PATH_HINT.search(value))


def ui_path_offenders(source: str, *, filename: str) -> list[str]:
    """Literals in ``source`` that assemble a UI path. Empty is the good case."""
    tree = ast.parse(source, filename=filename)
    return [
        f"{filename}:{node.lineno}: {node.value!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _is_ui_path_fragment(node.value)
    ]


def live_project_url(url: str) -> bool:
    """Is this a URL the Opik UI actually serves?

    True for a project-scoped page whose area the router defines, and for the
    backend redirect, which derives both workspace and project from the id it
    is given. False for everything else — including the paths that still
    resolve today by way of the compatibility shim.
    """
    if _SESSION_REDIRECT.match(url):
        return True
    match = _PROJECT_SCOPED.match(url)
    if match is None:
        return False
    area = match.group("area")
    # "diagnostics/resolved" is one area; "datasets/ds-1" is an area plus the
    # id of the thing on it. Try the longest reading first.
    return area in _LIVE_AREAS or area.split("/", 1)[0] in _LIVE_AREAS


@pytest.mark.parametrize(
    "url",
    [
        "https://opik.test/ws/projects/p-1/logs",
        "https://opik.test/ws/projects/p-1/logs?logsType=traces&trace=t-1",
        "https://opik.test/ws/projects/p-1/diagnostics/resolved?issue=i-1",
        "https://opik.test/ws/projects/p-1/datasets/ds-1",
        "https://opik.test/api/v1/session/redirect/projects/?trace_id=t-1&path=aHR0cA",
    ],
)
def test_live_project_url_accepts_the_shapes_the_ui_serves(url: str) -> None:
    assert live_project_url(url)


@pytest.mark.parametrize(
    ("url", "why"),
    [
        (
            "https://opik.test/ws/datasets/ds-1/items",
            "workspace-level: the shim resolves it against the reader's last project, "
            "and sends /datasets to the test suites page",
        ),
        (
            'https://opik.test/ws/experiments/ds-1/compare?experiments=["e-1"]',
            "workspace-level: the shape experiments_compare_url shipped",
        ),
        (
            "https://opik.test/ws/projects/p-1/traces?tab=logs&trace=t-1",
            "project-scoped but /traces is kept only to forward to /logs",
        ),
        (
            "https://opik.test/ws/prompts/pr-1",
            "workspace-level, and prompts moved under a project in v2",
        ),
    ],
)
def test_live_project_url_rejects_the_shapes_that_only_resolve_by_accident(
    url: str, why: str
) -> None:
    assert not live_project_url(url), why


def test_urls_are_built_only_where_the_area_set_is_enforced() -> None:
    """No module but the builder may spell a UI path.

    ``project_page_url`` refuses an area the router does not serve, so a link
    that goes through it cannot take the retired shape. A handler that formats
    the path itself gets no such check — which is exactly what happened to the
    experiment compare link — so the string literals are where this is caught.
    """
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        if path == _URL_BUILDER:
            continue
        offenders += ui_path_offenders(path.read_text(), filename=str(path.relative_to(_SRC)))
    assert not offenders, (
        "UI paths must be built by ui_links.project_page_url, which refuses an "
        "area the v2 router does not serve:\n  " + "\n  ".join(offenders)
    )


def test_the_guard_catches_a_hand_rolled_link() -> None:
    """The guard's own test: plant the exact drift it exists to prevent.

    Without this, a guard that had quietly stopped matching anything would
    still pass, every time, on a repository full of hand-rolled links.
    """
    planted = (
        "def link(base, ws, ds, runs):\n"
        '    return f"{base}/{ws}/experiments/{ds}/compare?experiments={runs}"\n'
    )
    assert ui_path_offenders(planted, filename="planted.py")


def test_the_guard_leaves_rest_routes_and_the_link_parser_alone() -> None:
    innocent = (
        'SUFFIX = "/items/experiments/items"\n'
        'COMPARE = re.compile(r"/experiments/([^/?#]+)/compare")\n'
        'DOC = "a Diagnostics link (…/projects/<pid>/diagnostics?issue=<id>)"\n'
    )
    assert ui_path_offenders(innocent, filename="innocent.py") == []
