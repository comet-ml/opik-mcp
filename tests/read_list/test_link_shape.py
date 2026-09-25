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
from typing import cast, get_args
from urllib.parse import unquote

import pytest

from opik_mcp.auth_context import OAUTH_ACCESS_TOKEN_PREFIX, inbound_authorization
from opik_mcp.config import Settings
from opik_mcp.opik_client import OpikListClient
from opik_mcp.read_list.decorations import link_note_for
from opik_mcp.read_list.entities.dataset import dataset_links
from opik_mcp.read_list.entities.experiment import experiment_links
from opik_mcp.read_list.entities.prompt import prompt_links
from opik_mcp.read_list.entities.span import span_links
from opik_mcp.read_list.entities.thread import thread_links
from opik_mcp.read_list.entities.trace import trace_links
from opik_mcp.read_list.handler import PageContext
from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.project_scope import remember_resolved_project, resolved_project
from opik_mcp.read_list.read_tool import _link_hint
from opik_mcp.read_list.size import size_header
from opik_mcp.read_list.ui_links import ProjectArea, row_link_template, view_link_note
from tests.factories import make_settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _ScoreNameClient:
    """Just enough of the client for a score_name listing."""

    async def list_project_score_names(self, project_id: str, /) -> dict[str, object]:
        return {"scores": [{"name": "helpfulness"}]}


_LIVE_AREAS = frozenset(get_args(ProjectArea))


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "opik_api_key": "k",
        "comet_workspace": "demo-ws",
        "opik_url": "https://opik.test/api/",
    }
    base.update(overrides)
    return make_settings(**base)


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


# --- ticket 02: the links we already emit, pointed at live routes ---------- #


def test_experiment_link_opens_the_compare_view_under_its_own_project() -> None:
    """The project is in the record the link function is already reading."""
    links = experiment_links(
        _settings(),
        {"id": "e-1", "dataset_id": "ds-1", "project_id": "p-7"},
    )
    assert links["url"] == (
        "https://opik.test/demo-ws/projects/p-7/experiments/ds-1/compare"
        "?experiments=%5B%22e-1%22%5D"
    )
    # The percent-encoded form is what the UI's address bar carries for a
    # compare view, so it is what a pasted link comes back as.
    assert unquote(links["url"].split("experiments=", 1)[1]) == '["e-1"]'
    assert live_project_url(links["url"])


def test_experiment_without_a_project_gets_no_link() -> None:
    """A run whose project the backend did not send is a run we cannot
    address: v2 has no page for it outside a project."""
    assert experiment_links(_settings(), {"id": "e-1", "dataset_id": "ds-1"}) == {}


def test_trace_link_opens_logs_directly_when_the_workspace_is_known() -> None:
    """The redirect lands on /projects/{id}/traces, which v2 keeps only to
    forward to /logs. We know the project from the record, so we can address
    the destination instead of the forwarder."""
    links = trace_links(_settings(), {"trace": {"id": "t-1", "project_id": "p-7"}})
    assert links["url"] == ("https://opik.test/demo-ws/projects/p-7/logs?logsType=traces&trace=t-1")
    assert live_project_url(links["url"])


def test_trace_link_falls_back_to_the_redirect_when_the_workspace_is_unknown() -> None:
    """Under an OAuth bearer introspection could not name, the direct URL
    cannot be built — and the redirect resolves the workspace server-side, so
    the session keeps its links instead of silently losing them."""
    token = inbound_authorization.set(f"Bearer {OAUTH_ACCESS_TOKEN_PREFIX}abc")
    try:
        links = trace_links(_settings(), {"trace": {"id": "t-1", "project_id": "p-7"}})
        assert "/v1/session/redirect/projects/" in links["url"]
        assert "trace_id=t-1" in links["url"]
        assert live_project_url(links["url"])
    finally:
        inbound_authorization.reset(token)


def test_trace_link_falls_back_to_the_redirect_without_a_project() -> None:
    """list('trace', …) rows carry no project_id, and a trace read of a record
    the backend did not scope still has an id — the redirect needs only that."""
    links = trace_links(_settings(), {"trace": {"id": "t-1"}})
    assert "/v1/session/redirect/projects/" in links["url"]


# --- ticket 03: threads and spans --------------------------------------- #


def test_thread_link_opens_logs_on_the_threads_view() -> None:
    links = thread_links(_settings(), {"thread": {"id": "th-1", "project_id": "p-7"}})
    assert links["url"] == (
        "https://opik.test/demo-ws/projects/p-7/logs?logsType=threads&thread=th-1"
    )
    assert live_project_url(links["url"])


def test_thread_without_a_project_gets_no_link() -> None:
    assert thread_links(_settings(), {"thread": {"id": "th-1"}}) == {}


def test_span_link_opens_its_trace_with_the_span_selected() -> None:
    """Verified against the running UI: a span is not a page. ``?span=`` is a
    selection *inside* an opened trace panel — on its own it does nothing, and
    the UI appends an empty ``span=`` when a trace is opened without one. So a
    span is addressed through the trace it belongs to."""
    links = span_links(
        _settings(),
        {"id": "s-1", "trace_id": "t-1", "project_id": "p-7"},
    )
    assert links["url"] == (
        "https://opik.test/demo-ws/projects/p-7/logs?logsType=traces&trace=t-1&span=s-1"
    )
    assert live_project_url(links["url"])


def test_span_without_its_trace_gets_no_link() -> None:
    """Nothing to open it inside, and the spans view does not open a panel."""
    assert span_links(_settings(), {"id": "s-1", "project_id": "p-7"}) == {}


# --- tickets 04 and 05: scoped entities, and the things that are not pages - #


def test_dataset_links_to_its_page_when_it_is_project_scoped() -> None:
    links = dataset_links(_settings(), {"id": "ds-1", "project_id": "p-7", "name": "cases"})
    assert links["url"] == "https://opik.test/demo-ws/projects/p-7/datasets/ds-1"
    assert live_project_url(links["url"])


def test_a_workspace_level_dataset_says_why_it_has_no_link() -> None:
    """v2 has no workspace-level route and the backend filters these listings
    strictly on project, so there is no page to open — and the reader deserves
    that sentence rather than wondering why this record alone has no link."""
    links = dataset_links(_settings(), {"id": "ds-1", "name": "cases"})
    assert "url" not in links
    assert "project" in links["url_absent"]


def test_prompt_links_to_its_page_when_it_is_project_scoped() -> None:
    links = prompt_links(
        _settings(), {"prompt": {"id": "pr-1", "project_id": "p-7", "name": "judge"}}
    )
    assert links["url"] == "https://opik.test/demo-ws/projects/p-7/prompts/pr-1"


def test_a_workspace_level_prompt_says_why_it_has_no_link() -> None:
    links = prompt_links(_settings(), {"prompt": {"id": "pr-1", "name": "judge"}})
    assert "url" not in links
    assert "project" in links["url_absent"]


def test_a_view_tier_link_names_the_page_it_opens() -> None:
    """A score name is a column, not a page. The link still helps, but the
    label has to stop it reading as though the score is waiting behind it."""
    note = view_link_note(_settings(), "score_name", "p-7")
    assert note is not None
    assert note["url"] == "https://opik.test/demo-ws/projects/p-7/logs"
    assert "column" in note["url_opens"].lower()
    assert live_project_url(note["url"])


@pytest.mark.parametrize(
    ("entity", "area"),
    [
        ("score_name", "logs"),
        ("online_rule", "online-evaluation"),
        ("project_metric", "dashboards"),
    ],
)
def test_every_view_tier_entity_has_a_page_and_a_label(entity: str, area: str) -> None:
    note = view_link_note(_settings(), entity, "p-7")
    assert note is not None
    assert note["url"].endswith(f"/projects/p-7/{area}")
    assert note["url_opens"]


def test_a_view_tier_link_is_absent_without_a_project() -> None:
    assert view_link_note(_settings(), "score_name", "") is None


def test_a_view_tier_label_does_not_describe_rows_an_empty_page_has_none_of() -> None:
    """ "where this rule is a row" is false on a page with no rows. The link is
    still worth having — it is where the reader goes to make one — so the
    label changes rather than the link disappearing."""
    full = view_link_note(_settings(), "online_rule", "p-7")
    empty = view_link_note(_settings(), "online_rule", "p-7", empty=True)
    assert full is not None
    assert empty is not None
    assert full["url"] == empty["url"]
    assert "this rule" in full["url_opens"]
    assert "this rule" not in empty["url_opens"]


# --- ticket 06: a page of rows, without a link per row -------------------- #


@pytest.mark.parametrize(
    ("entity", "expected"),
    [
        ("trace", "https://opik.test/demo-ws/projects/p-7/logs?logsType=traces&trace={id}"),
        (
            "span",
            "https://opik.test/demo-ws/projects/p-7/logs"
            "?logsType=traces&trace={trace_id}&span={id}",
        ),
        ("thread", "https://opik.test/demo-ws/projects/p-7/logs?logsType=threads&thread={id}"),
    ],
)
def test_a_project_scoped_page_carries_one_template_for_every_row(
    entity: str, expected: str
) -> None:
    """Every row shares the project, so only the id varies — one template for
    the page costs what one url would, instead of one per row."""
    note = row_link_template(_settings(), entity, "p-7")
    assert note is not None
    assert note["url_template"] == expected
    # the template with its slots filled must be a link the UI serves
    filled = expected.replace("{id}", "x-1").replace("{trace_id}", "t-1")
    assert live_project_url(filled)


def test_the_project_list_fills_its_template_from_the_row_id() -> None:
    """A project list's rows differ by project — but the project *is* the row,
    so one template still serves the page."""
    note = row_link_template(_settings(), "project", None)
    assert note is not None
    assert note["url_template"] == "https://opik.test/demo-ws/projects/{id}/logs"


def test_no_template_where_the_row_cannot_fill_one() -> None:
    """An experiment's address needs its project and its dataset, and the row
    shows neither — it shows dataset_name. A template nothing can fill is
    worse than none, so the entity gets no template rather than two more id
    columns on every row."""
    assert row_link_template(_settings(), "experiment", "p-7") is None


def test_no_template_without_a_project() -> None:
    assert row_link_template(_settings(), "trace", None) is None


# --- ticket 08: the answer names its own link ----------------------------- #


def test_the_header_says_the_answer_has_a_link_and_what_to_call_it() -> None:
    """The rule that an id is never shown bare lives in the instructions, and
    not every host passes those to the model. The tool result always reaches
    it, so the header carries the same instruction where it cannot be lost —
    without the url, which is already one line below."""
    header = size_header("experiment", "e-1", 120, link_as="baseline-seed")
    assert "open as a link named 'baseline-seed'" in header
    assert "http" not in header


def test_the_header_is_unchanged_for_an_answer_with_no_link() -> None:
    assert size_header("dataset", "ds-1", 120) == "[read: dataset ds-1 | 120 tok]"


def test_an_unnamed_record_gets_the_generic_link_text() -> None:
    header = size_header("span", "s-1", 120, link_as="Open in Opik")
    assert "open as a link named 'Open in Opik'" in header


def test_a_projection_that_drops_the_url_drops_the_header_promise_too() -> None:
    """Projection runs after the links are attached, so a caller who does not
    name `url` does not get one. The header must not say otherwise: a promise
    of a link the payload has no url for sends the agent looking for it."""
    record = {"id": "e-1", "name": "baseline-seed", "url": "https://opik.test/x"}
    assert _link_hint("experiment", record)["link_as"] == "baseline-seed"
    assert _link_hint("experiment", {k: v for k, v in record.items() if k != "url"}) == {}


def test_project_metric_has_a_page_and_is_reachable_through_the_runner() -> None:
    """A metric answers through its runner, not the collection path, and that
    path never called page_note_fn — so the entry for it in the view table was
    defined and dead. Both halves are asserted: the label exists, and the
    handler declares the hook that now gets called."""
    from opik_mcp.read_list.registry import ENTITY_REGISTRY

    note = view_link_note(_settings(), "project_metric", "p-7")
    assert note is not None
    assert note["url"].endswith("/projects/p-7/dashboards")
    assert ENTITY_REGISTRY["project_metric"].page_note_fn is not None


@pytest.mark.anyio
async def test_a_list_scoped_by_name_reads_the_project_off_its_own_rows() -> None:
    """Found by measuring a page rather than reading the code: the note asked
    for ctx.project_id, and a caller who scoped by project_name has none — so
    the commoner spelling of the commonest list came back with no link.

    The rows already say which project they are in, so nothing is looked up.
    That matters: these listings take a name precisely so they do not have to
    round-trip it into an id, and a decoration does not get to spend a call
    the page itself declined to.
    """

    class _Client:
        calls = 0

        async def list_projects(self, **kw: object) -> dict[str, object]:
            type(self).calls += 1
            return {"content": [], "total": 0}

    note = await link_note_for("trace")(
        cast("OpikListClient", _Client()),
        _settings(),
        PageContext(
            project_id=None,
            project_name="checkout",
            rows=({"id": "t-1", "project_id": "p-7"},),
        ),
    )
    assert note is not None
    assert "/projects/p-7/logs?logsType=traces&trace={id}" in note
    assert _Client.calls == 0, "the rows had it; nothing should have been asked"


@pytest.mark.anyio
async def test_a_page_whose_rows_name_no_project_simply_carries_no_link() -> None:
    """No link is the right answer here, not a looked-up one."""
    # The resolved-project ContextVar lives in the anyio runner task, which the
    # sync autouse fixtures cannot reach; an earlier list call may have set it.
    remember_resolved_project(None)
    note = await link_note_for("trace")(
        cast("OpikListClient", object()),
        _settings(),
        PageContext(project_id=None, project_name="checkout", rows=({"id": "t-1"},)),
    )
    assert note is None


@pytest.mark.anyio
async def test_a_score_name_page_scoped_by_name_keeps_its_link() -> None:
    """The rows of this listing are bare names — no id, no project, ever — so
    reading the project off them cannot work here, and scoping by name lost
    the link entirely.

    The listing already resolved the project to call its endpoint. That answer
    is what the note uses, so the page costs exactly what it did before and
    the name-scoped call is no poorer than the id-scoped one.
    """
    remember_resolved_project("p-7")
    note = await link_note_for("score_name")(
        cast("OpikListClient", object()),
        _settings(),
        PageContext(project_id=None, project_name="checkout", rows=({"name": "helpfulness"},)),
    )
    assert note is not None
    assert "/projects/p-7/logs" in note
    assert "column" in note


@pytest.mark.anyio
async def test_one_page_never_inherits_another_page_s_project() -> None:
    """The reuse above is per call and must not outlive it. A project left
    over from a previous listing would build a link into the wrong project —
    which is the failure this whole feature exists to stop, arriving by the
    back door."""
    remember_resolved_project("p-from-an-earlier-call")
    await run_list(
        "score_name", project_id="p-7", client=cast("OpikListClient", _ScoreNameClient())
    )
    assert resolved_project() is None, "run_list clears it before doing anything"


def test_a_url_column_is_never_cut_to_fit() -> None:
    """A truncated link is worse than no link: it looks like an address and
    opens nothing. The table cuts wide cells at 60 characters and every Opik
    url is longer than that, so the column has to be exempt — the cut exists
    to keep a table scannable, and a url is not read, it is clicked."""
    from opik_mcp.read_list.list_tool import _render_cell

    url = (
        "https://www.comet.com/opik/ws/projects/01a0c38f-4119-77ff-a0d8-0994aaa47fd1"
        "/experiments/01a0c38f-4101-7256-a373-27ed81e31c7c/compare"
    )
    kept, was_cut = _render_cell("url", url, cell_limit=60)
    assert kept == url
    assert was_cut is False

    trimmed, cut_it = _render_cell("name", "x" * 200, cell_limit=60)
    assert trimmed.endswith("...")
    assert cut_it is True


@pytest.mark.anyio
async def test_a_failed_call_leaves_nothing_for_the_next_one_to_pick_up() -> None:
    """The clear happens at the start of a call, not in a finally, so it holds
    whatever the previous call did — crashed, timed out, or never ran. That is
    the point: correctness here cannot depend on cleanup having run.

    Asserted the way it matters: a project left behind by a failure does not
    end up in the next page's link.
    """

    class _Boom:
        async def list_project_score_names(self, project_id: str, /) -> dict[str, object]:
            raise RuntimeError("backend down")

    remember_resolved_project("p-stale")
    with pytest.raises(RuntimeError):
        await run_list("score_name", project_id="p-7", client=cast("OpikListClient", _Boom()))

    out = await run_list(
        "score_name", project_id="p-7", client=cast("OpikListClient", _ScoreNameClient())
    )
    assert "/projects/p-7/logs" in out
    assert "p-stale" not in out


@pytest.mark.anyio
async def test_a_runner_call_clears_it_too() -> None:
    """A metric answers through the runner, which is dispatched from the same
    entry point — so the clear has to happen before the branch, not inside
    the collection path."""

    class _Metrics:
        async def get_project_metrics(
            self, project_id: str, /, **body: object
        ) -> dict[str, object]:
            return {"results": []}

    remember_resolved_project("p-stale")
    await run_list(
        "project_metric",
        project_id="p-7",
        metric_type="trace_count",
        client=cast("OpikListClient", _Metrics()),
    )
    assert resolved_project() is None


@pytest.mark.anyio
async def test_an_experiment_page_that_cannot_link_prints_no_url_column() -> None:
    """A column of empty cells under a header called `url` says the wrong
    thing twice: that these runs have addresses, and that we lost them. The
    ticket's words are "a list that cannot be linked carries neither, and says
    nothing about links", so the column appears only when some row filled it.
    """

    class _Experiments:
        async def list_experiments(self, **kw: object) -> dict[str, object]:
            return {
                "content": [{"id": "e-1", "name": "nightly", "dataset_id": "ds-1"}],
                "total": 1,
            }

    # No project_id on the record, so no row can be addressed.
    out = await run_list("experiment", client=cast("OpikListClient", _Experiments()))
    assert "nightly" in out
    assert "url" not in out.splitlines()[2], out.splitlines()[2]


@pytest.mark.anyio
async def test_a_case_listing_links_to_the_page_its_dataset_is_on() -> None:
    """A case has no page; its dataset does. The rows name the dataset and
    nothing names the project, so this is the one listing where the link
    costs a call — which is what the note hook is handed a client for.
    """

    class _Client:
        async def get_dataset(self, dataset_id: str, /) -> dict[str, object]:
            return {"id": dataset_id, "name": "cases", "project_id": "p-7"}

    note = await link_note_for("dataset_item")(
        cast("OpikListClient", _Client()),
        _settings(),
        PageContext(parent_id="ds-1", rows=({"id": "c-1"},)),
    )
    assert note is not None
    assert "/projects/p-7/datasets/ds-1/items" in note
    assert "Open in Opik" in note


@pytest.mark.anyio
async def test_a_case_listing_of_a_workspace_level_dataset_says_nothing() -> None:
    """Its dataset has no page either, so there is nothing to point at and no
    sentence worth spending — the parent read is where that is explained."""

    class _Unscoped:
        async def get_dataset(self, dataset_id: str, /) -> dict[str, object]:
            return {"id": dataset_id, "name": "cases"}

    note = await link_note_for("dataset_item")(
        cast("OpikListClient", _Unscoped()),
        _settings(),
        PageContext(parent_id="ds-1", rows=({"id": "c-1"},)),
    )
    assert note is None


@pytest.mark.anyio
async def test_a_case_listing_survives_a_parent_that_cannot_be_read() -> None:
    """The rule every decoration lives under: it must never be the reason an
    answered page comes back as an error."""

    class _Boom:
        async def get_dataset(self, dataset_id: str, /) -> dict[str, object]:
            raise RuntimeError("backend down")

    note = await link_note_for("dataset_item")(
        cast("OpikListClient", _Boom()),
        _settings(),
        PageContext(parent_id="ds-1", rows=({"id": "c-1"},)),
    )
    assert note is None


def test_every_entity_with_a_link_has_it_wired_to_its_handler() -> None:
    """A factory nobody calls is a feature nobody has.

    The parent-page note was written, tested by calling `link_note_for`
    directly, and never attached to the two handlers that needed it — so the
    tests passed and the listing shipped without the link. Asserted through
    the registry from now on, which is the only path a real call takes.
    """
    from opik_mcp.read_list.decorations import _PARENT_PAGE
    from opik_mcp.read_list.registry import ENTITY_REGISTRY

    unwired = [e for e in _PARENT_PAGE if ENTITY_REGISTRY[e].page_note_fn is None]
    assert not unwired, f"link logic exists but no handler calls it: {', '.join(unwired)}"
