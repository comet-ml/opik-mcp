"""What every per-entity read file needs: calling a tool, and the checks they share."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp import ClientSession

from tests.hermetic.servers import API_KEY, HttpServer, http_session, result_text
from tests.hermetic.stub_backend import StubBackend
from tests.read_list.test_link_shape import live_project_url

#: One tool call: the tool and its arguments.
Call = tuple[str, dict[str, object]]

URL = re.compile(r"https?://[^\s\"',)]+")

#: What a refusal must never carry: the stub's own error bodies, which stand
#: for untrusted backend text, and a REST path, which is no name the caller
#: can use.
_BACKEND_DETAIL = ("stub failure", "stub internals", "stub has no route", "/v1/private", "/api/")


@dataclass(frozen=True)
class Http:
    """A stub and the Streamable HTTP server pointed at it, shared by a package."""

    backend: StubBackend
    server: HttpServer

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ClientSession]:
        async with http_session(self.server, bearer=API_KEY) as session:
            yield session


async def text(session: ClientSession, tool: str, args: dict[str, object]) -> str:
    """The answer as the host hands it to the model, answered or refused."""
    return result_text(await session.call_tool(tool, args))


async def call(session: ClientSession, tool: str, **args: object) -> str:
    result = await session.call_tool(tool, args)
    answer = "\n".join(part.text for part in result.content if hasattr(part, "text"))
    assert not result.isError, f"{tool}({args}) was refused: {answer}"
    return answer


async def refuse(session: ClientSession, tool: str, **args: object) -> str:
    result = await session.call_tool(tool, args)
    answer = "\n".join(part.text for part in result.content if hasattr(part, "text"))
    assert result.isError, f"{tool}({args}) was answered, expected a refusal: {answer}"
    return answer


def payload(answer: str) -> dict[str, object]:
    """A read's JSON body, below its header line."""
    body = answer.split("\n", 1)[1] if "\n" in answer else answer
    parsed = json.loads(body)
    assert isinstance(parsed, dict), f"the body is not a JSON object: {body[:300]}"
    return {str(key): value for key, value in parsed.items()}


def retired_addresses(answers: Iterable[str]) -> list[str]:
    """Every url in the answers that is not a page the UI serves.

    A template's slots stand in for a row's columns; they are filled so the
    shape check sees the address a caller would actually open.
    """
    return [
        url
        for answer in answers
        for url in URL.findall(answer)
        if not live_project_url(re.sub(r"\{[a-z_]+\}", "x", url))
    ]


async def assert_sized_envelopes(http: Http, entity: str, calls: Sequence[Call]) -> None:
    """Each answer opens with a header stating its size, over the entity's envelope."""
    header = re.compile(rf"^\[(read|list): {entity}(?: [^|\]]+)? \| [\d,]+ tok(?: \| .*)?\]$")
    async with http.session() as session:
        for tool, args in calls:
            answer = await call(session, tool, entity_type=entity, **args)
            first, _, body = answer.partition("\n")
            assert header.fullmatch(first), f"{tool}({entity!r}) header states no size: {first}"
            assert first.startswith(f"[{tool}:"), f"{tool}({entity!r}) header names {first}"
            if tool == "read":
                assert payload(answer), f"read({entity!r}) has an empty body"
            else:
                assert body.startswith(("Found ", "time | ")), (
                    f"list({entity!r}) body is not a page: {body[:300]}"
                )


async def assert_refusals_hide_the_backend(http: Http, entity: str, calls: Sequence[Call]) -> None:
    """A 500 and a 400 from every route, and neither reaches the caller as text."""
    async with http.session() as session:
        for knob in ("failing", "rejecting"):
            setattr(http.backend, knob, {"/v1/private"})
            for tool, args in calls:
                refusal = await refuse(session, tool, entity_type=entity, **args)
                leaked = [detail for detail in _BACKEND_DETAIL if detail in refusal]
                assert not leaked, f"{tool}({args}) with every route {knob}: {leaked} in {refusal}"
                assert f"127.0.0.1:{http.backend.port}" not in refusal, refusal
            setattr(http.backend, knob, set())
