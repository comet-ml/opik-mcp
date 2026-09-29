"""Cost intelligence mode serves one project, whichever way a call names it."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import cast

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from opik_mcp.client.protocols import OpikReadClient
from opik_mcp.cost_intelligence import FIXED_PROJECT, WORKSPACE_PREFIX
from opik_mcp.read_list.list_tool import run_list
from opik_mcp.read_list.read_tool import run_read
from opik_mcp.read_list.registry import ENTITY_REGISTRY
from tests.factories import make_settings

pytestmark = pytest.mark.anyio

SPEND = make_settings(opik_workspace=f"{WORKSPACE_PREFIX}org__", opik_api_key="k")
DEFAULT = make_settings(opik_workspace="team", opik_api_key="k")
FIXED_ID = "0190a3c4-0000-7000-8000-00000000000a"
OTHER_ID = "0190a3c4-0000-7000-8000-00000000000b"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class _Fake:
    projects: list[dict[str, object]] = field(
        default_factory=lambda: [
            {"id": OTHER_ID, "name": "other-project"},
            {"id": FIXED_ID, "name": FIXED_PROJECT},
        ]
    )
    records: dict[str, dict[str, object]] = field(default_factory=dict)
    trace_list_kwargs: dict[str, object] = field(default_factory=dict)

    async def list_projects(self, *, name: str | None = None, **_kw: object) -> dict[str, object]:
        rows = [p for p in self.projects if name is None or name.lower() in str(p["name"]).lower()]
        return {"content": rows, "total": len(self.projects), "page": 1, "size": len(rows)}

    async def get_trace(self, trace_id: str) -> dict[str, object]:
        return self.records[trace_id]

    async def get_span(self, span_id: str) -> dict[str, object]:
        return self.records[span_id]

    async def list_spans(self, **_kw: object) -> dict[str, object]:
        return {"content": [], "total": 0}

    async def list_traces(self, **kw: object) -> dict[str, object]:
        self.trace_list_kwargs = kw
        row = {"id": "t1", "name": "a trace", "project_id": FIXED_ID}
        return {"content": [row], "total": 1, "page": 1, "size": 1}


def as_client(fake: _Fake) -> OpikReadClient:
    return cast("OpikReadClient", fake)


def _trace(project_id: str) -> dict[str, object]:
    return {"id": "t1", "name": "a trace", "project_id": project_id}


async def test_a_list_without_a_project_runs_in_the_fixed_one() -> None:
    fake = _Fake()
    await run_list("trace", settings=SPEND, client=as_client(fake))
    assert fake.trace_list_kwargs["project_name"] == FIXED_PROJECT


async def test_a_list_outside_cost_intelligence_still_needs_its_project() -> None:
    with pytest.raises(ToolError, match="requires project_id"):
        await run_list("trace", settings=DEFAULT, client=as_client(_Fake()))


async def test_another_project_name_is_refused_on_list_and_read() -> None:
    fake = _Fake()
    with pytest.raises(ToolError, match=f"only serves the `{FIXED_PROJECT}` project"):
        await run_list(
            "trace", project_name="other-project", settings=SPEND, client=as_client(fake)
        )
    with pytest.raises(ToolError, match=f"only serves the `{FIXED_PROJECT}` project"):
        await run_read(
            "thread", "th", project_name="other-project", settings=SPEND, client=as_client(fake)
        )


async def test_another_project_id_is_refused_and_the_fixed_one_is_accepted() -> None:
    fake = _Fake()
    with pytest.raises(ToolError, match=f"only serves the `{FIXED_PROJECT}` project"):
        await run_list("trace", project_id=OTHER_ID, settings=SPEND, client=as_client(fake))
    await run_list("trace", project_id=FIXED_ID, settings=SPEND, client=as_client(fake))
    assert fake.trace_list_kwargs["project_id"] == FIXED_ID


async def test_a_metric_call_is_confined_like_a_collection() -> None:
    with pytest.raises(ToolError, match=f"only serves the `{FIXED_PROJECT}` project"):
        await run_list(
            "project_metric",
            project_id=OTHER_ID,
            metric_type="trace_count",
            settings=SPEND,
            client=as_client(_Fake()),
        )


async def test_a_project_listing_keeps_only_the_fixed_project_and_its_total() -> None:
    out = await run_list("project", settings=SPEND, client=as_client(_Fake()))
    assert FIXED_PROJECT in out
    assert "other-project" not in out
    assert "showing 1 of 1" in out


async def test_a_project_listing_in_the_default_mode_shows_every_project() -> None:
    out = await run_list("project", settings=DEFAULT, client=as_client(_Fake()))
    assert "other-project" in out
    assert FIXED_PROJECT in out


async def test_reading_another_project_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fetch(_client: object, entity_id: str, **_kw: object) -> dict[str, object]:
        return {"project": {"id": entity_id, "name": "whatever"}}

    monkeypatch.setitem(
        ENTITY_REGISTRY, "project", replace(ENTITY_REGISTRY["project"], fetch_fn=fetch)
    )
    with pytest.raises(ToolError, match=f"only serves the `{FIXED_PROJECT}` project"):
        await run_read("project", OTHER_ID, settings=SPEND, client=as_client(_Fake()))
    assert FIXED_ID in await run_read(
        "project", FIXED_ID, settings=SPEND, client=as_client(_Fake())
    )


@pytest.mark.parametrize("entity_type", ["trace", "span"])
async def test_a_fetched_record_of_another_project_is_refused(entity_type: str) -> None:
    fake = _Fake(records={"r1": _trace(OTHER_ID), "r2": _trace(FIXED_ID)})
    with pytest.raises(ToolError, match=f"only serves the `{FIXED_PROJECT}` project"):
        await run_read(entity_type, "r1", settings=SPEND, client=as_client(fake))
    assert "a trace" in await run_read(entity_type, "r2", settings=SPEND, client=as_client(fake))


async def test_a_record_of_another_project_is_readable_in_the_default_mode() -> None:
    fake = _Fake(records={"r1": _trace(OTHER_ID)})
    assert "a trace" in await run_read("trace", "r1", settings=DEFAULT, client=as_client(fake))


async def test_a_link_carrying_another_project_is_refused() -> None:
    link = f"opik://projects/{OTHER_ID}/threads/th1"
    with pytest.raises(ToolError, match=f"only serves the `{FIXED_PROJECT}` project"):
        await run_read("thread", link, settings=SPEND, client=as_client(_Fake()))


async def test_a_missing_fixed_project_gets_the_mode_refusal_without_other_names() -> None:
    fake = _Fake(projects=[{"id": OTHER_ID, "name": "other-project"}])
    for call in (
        run_list("project", settings=SPEND, client=as_client(fake)),
        run_read("thread", "th", project_id=FIXED_ID, settings=SPEND, client=as_client(fake)),
    ):
        with pytest.raises(ToolError) as exc:
            await call
        assert "no `claude-code` project yet" in str(exc.value)
        assert "other-project" not in str(exc.value)


async def test_a_project_listing_by_another_name_says_only_the_fixed_project_is_served() -> None:
    with pytest.raises(ToolError, match=f"only serves the `{FIXED_PROJECT}` project") as exc:
        await run_list("project", name="other", settings=SPEND, client=as_client(_Fake()))
    assert "no `claude-code` project yet" not in str(exc.value)
    assert FIXED_PROJECT in await run_list(
        "project", name="claude", settings=SPEND, client=as_client(_Fake())
    )


async def test_a_project_page_past_the_fixed_row_is_not_a_missing_project() -> None:
    class _PastTheEnd(_Fake):
        async def list_projects(self, **_kw: object) -> dict[str, object]:
            return {"content": [], "total": 1, "page": 2, "size": 0}

    out = await run_list("project", page=2, settings=SPEND, client=as_client(_PastTheEnd()))
    assert "no `claude-code` project yet" not in out


async def test_reading_a_project_by_another_name_is_refused_before_any_fetch() -> None:
    class _NoCalls(_Fake):
        async def list_projects(self, **_kw: object) -> dict[str, object]:
            raise AssertionError("fetched before refusing")

    with pytest.raises(ToolError, match=f"only serves the `{FIXED_PROJECT}` project"):
        await run_read("project", "other-project", settings=SPEND, client=as_client(_NoCalls()))


async def test_reading_another_projects_id_is_refused_before_any_fetch() -> None:
    class _NoFetch(_Fake):
        async def get_project(self, *_a: object, **_kw: object) -> dict[str, object]:
            raise AssertionError("fetched before refusing")

        def __getattr__(self, name: str) -> object:
            if name.startswith("_"):
                raise AttributeError(name)
            raise AssertionError(f"called {name} before refusing")

    with pytest.raises(ToolError, match=f"only serves the `{FIXED_PROJECT}` project"):
        await run_read("project", OTHER_ID, settings=SPEND, client=as_client(_NoFetch()))


async def test_a_project_page_past_the_fixed_row_still_counts_one_project() -> None:
    class _PastTheEnd(_Fake):
        async def list_projects(self, **_kw: object) -> dict[str, object]:
            return {"content": [], "total": 40, "page": 2, "size": 0}

    out = await run_list("project", page=2, settings=SPEND, client=as_client(_PastTheEnd()))
    assert "40" not in out


@dataclass
class _ProjectRead(_Fake):
    """Answers every other call with an empty page and remembers what was asked."""

    called: list[str] = field(default_factory=list)

    async def get_project(self, *_a: object, **_kw: object) -> dict[str, object]:
        return {"id": FIXED_ID, "name": FIXED_PROJECT}

    def __getattr__(self, name: str) -> object:
        async def call(*_a: object, **_kw: object) -> dict[str, object]:
            self.called.append(name)
            return {"content": [], "total": 0}

        return call


HIDDEN_LEGS = {"list_project_activities", "list_automation_rules"}


async def test_the_modes_project_read_names_no_hidden_type() -> None:
    fake = _ProjectRead()
    out = await run_read("project", FIXED_PROJECT, settings=SPEND, client=as_client(fake))
    assert not HIDDEN_LEGS & set(fake.called)
    for hidden in ("contains", "online_rules", "experiment"):
        assert hidden not in out
    assert "summary" in out


async def test_the_default_project_read_still_fetches_those_legs() -> None:
    fake = _ProjectRead()
    await run_read("project", FIXED_PROJECT, settings=DEFAULT, client=as_client(fake))
    assert set(fake.called) >= HIDDEN_LEGS


@dataclass
class _RecordsName(_Fake):
    names: list[str | None] = field(default_factory=list)

    async def list_projects(self, *, name: str | None = None, **kw: object) -> dict[str, object]:
        self.names.append(name)
        return await super().list_projects(name=name, **kw)


async def test_a_name_substring_never_widens_the_backend_page() -> None:
    fake = _RecordsName()
    out = await run_list("project", name="code", settings=SPEND, client=as_client(fake))
    assert FIXED_PROJECT in out
    assert set(fake.names) == {FIXED_PROJECT}


@pytest.mark.parametrize("twin_first", [True, False])
async def test_a_differently_cased_twin_project_is_not_listed(twin_first: bool) -> None:
    twin: dict[str, object] = {"id": OTHER_ID, "name": "Claude-Code"}
    fixed: dict[str, object] = {"id": FIXED_ID, "name": FIXED_PROJECT}
    fake = _Fake(projects=[twin, fixed] if twin_first else [fixed, twin])
    out = await run_list("project", settings=SPEND, client=as_client(fake))
    assert FIXED_ID in out
    assert OTHER_ID not in out
    assert "showing 1 of 1" in out


async def test_a_lone_differently_cased_project_is_the_fixed_one_on_list_and_read() -> None:
    fake = _Fake(projects=[{"id": FIXED_ID, "name": "Claude-Code"}])
    assert FIXED_ID in await run_list("project", settings=SPEND, client=as_client(fake))
    with pytest.raises(ToolError, match="only serves"):
        await run_read("project", OTHER_ID, settings=SPEND, client=as_client(fake))
