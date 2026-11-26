"""The `opik-price` skill's helper turns a sheet into a safe whole-list save.

The backend replaces the organization's whole price list on every save, so the
helper must send every saved row back, convert units exactly, and never save a
list the user didn't preview. These tests drive its command line against a stub
Opik, the way an agent runs it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Protocol, override

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2] / "plugins/opik/skills/opik-price/scripts/model_prices.py"
)
PRICES = "/api/v1/private/model-prices"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("opik_price_model_prices", SCRIPT)
    assert spec is not None, f"can't load {SCRIPT}"
    assert spec.loader is not None, f"can't load {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


model_prices = _load_script()


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    headers: dict[str, str]
    body: object


@dataclass
class StubOpik:
    url: str = ""
    answers: dict[tuple[str, str], list[tuple[int, object]]] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)

    def answer(self, method: str, path: str, status: int, body: object) -> None:
        self.answers.setdefault((method, path), []).append((status, body))

    def take(self, method: str, path: str) -> tuple[int, object]:
        queue = self.answers.get((method, path), [(404, {"message": "no stub"})])
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def sent(self, method: str, path: str) -> list[Call]:
        return [call for call in self.calls if call.method == method and call.path == path]


def _handler(stub: StubOpik) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _reply(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            body: object = json.loads(raw) if raw else None
            stub.calls.append(Call(self.command, self.path, dict(self.headers.items()), body))
            status, answer = stub.take(self.command, self.path)
            payload = json.dumps(answer).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_PUT = do_POST = _reply

        @override
        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


@pytest.fixture
def opik(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[StubOpik]:
    stub = StubOpik()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(stub))
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    stub.url = f"http://127.0.0.1:{server.server_address[1]}/api"
    monkeypatch.setattr(model_prices, "connection_from_sdk", lambda: None)
    monkeypatch.setenv("OPIK_CONFIG_PATH", str(tmp_path / "missing.config"))
    monkeypatch.setenv("OPIK_URL_OVERRIDE", stub.url)
    monkeypatch.setenv("OPIK_WORKSPACE", "team")
    monkeypatch.setenv("OPIK_API_KEY", "secret-key-123")
    yield stub
    server.shutdown()
    server.server_close()


class RunScript(Protocol):
    def __call__(self, *args: str) -> tuple[int, str]: ...


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str]) -> RunScript:
    def invoke(*args: str) -> tuple[int, str]:
        code = int(model_prices.main(list(args)))
        captured = capsys.readouterr()
        return code, captured.out + captured.err

    return invoke


def _sheet(tmp_path: Path, document: object) -> str:
    path = tmp_path / "rows.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


def _put_rows(call: Call) -> list[dict[str, object]]:
    body = call.body
    assert isinstance(body, dict), f"PUT body is not an object: {body!r}"
    rows = body["rows"]
    assert isinstance(rows, list)
    return [row for row in rows if isinstance(row, dict)]


GPT_4O = {
    "id": "1",
    "model": "gpt-4o",
    "provider": "openai",
    "kind": "fixed",
    "input_price": "2",
    "output_price": "8",
    "note": "contract A",
    "created_by": "admin",
}
ACME_X = {"id": "2", "model": "acme-x", "provider": "acme", "kind": "fixed", "input_price": "1"}
SAVED = {"version": 6, "rows": [GPT_4O, ACME_X]}
NOTHING_CHANGED = {
    "version": 6,
    "saved": False,
    "created": [],
    "updated": [],
    "deleted": [],
    "unchanged": 2,
    "warnings": [],
}


def test_per_token_prices_arrive_per_million_without_float_noise(
    opik: StubOpik, run: RunScript, tmp_path: Path
) -> None:
    opik.answer("GET", PRICES, 200, {"version": 0, "rows": []})
    opik.answer("PUT", f"{PRICES}?dry_run=true", 200, NOTHING_CHANGED)
    sheet = _sheet(
        tmp_path,
        {
            "unit": "per_token",
            "rows": [
                {"model": "m1", "provider": "acme", "input": 3e-06, "output": "0.0000025"},
                {"model": "m2", "provider": "acme", "input": "$0.0025", "unit": "per_1k"},
            ],
        },
    )

    run("preview", sheet)

    rows = _put_rows(opik.sent("PUT", f"{PRICES}?dry_run=true")[0])
    assert [(row["model"], row.get("input_price"), row.get("output_price")) for row in rows] == [
        ("m1", "3", "2.5"),
        ("m2", "2.5", None),
    ]


def test_a_sheet_row_changes_only_its_own_fields_and_every_other_row_is_sent_back(
    opik: StubOpik, run: RunScript, tmp_path: Path
) -> None:
    opik.answer("GET", PRICES, 200, SAVED)
    opik.answer("PUT", f"{PRICES}?dry_run=true", 200, NOTHING_CHANGED)
    sheet = _sheet(tmp_path, {"rows": [{"model": "GPT-4o", "provider": "OpenAI", "output": 6}]})

    run("preview", sheet)

    rows = _put_rows(opik.sent("PUT", f"{PRICES}?dry_run=true")[0])
    assert rows == [
        {
            "model": "gpt-4o",
            "provider": "openai",
            "kind": "fixed",
            "input_price": "2",
            "output_price": "6",
            "note": "contract A",
        },
        {"model": "acme-x", "provider": "acme", "kind": "fixed", "input_price": "1"},
    ]


def test_the_preview_shows_each_change_against_its_sheet_row(
    opik: StubOpik, run: RunScript, tmp_path: Path
) -> None:
    opik.answer("GET", PRICES, 200, SAVED)
    before = GPT_4O
    opik.answer(
        "PUT",
        f"{PRICES}?dry_run=true",
        200,
        {
            **NOTHING_CHANGED,
            "updated": [{"before": before, "after": {**before, "output_price": "6"}}],
            "created": [
                {"model": "acme-y", "provider": "acme", "kind": "fixed", "input_price": "5"}
            ],
            "unchanged": 1,
            "warnings": [{"row": 2, "field": "model", "code": "no_base", "message": "No built-in"}],
        },
    )
    sheet = _sheet(
        tmp_path,
        {
            "rows": [
                {"ref": "Sheet1 row 4", "model": "gpt-4o", "provider": "openai", "output": 6},
                {"ref": "Sheet1 row 5", "model": "acme-y", "provider": "acme", "input": 5},
                {"ref": "Sheet1 row 9", "skip": "batch prices aren't supported"},
            ]
        },
    )

    code, out = run("preview", sheet)

    assert code == 0, out
    assert "UPDATE   gpt-4o · openai   [Sheet1 row 4]\n         output 8 → 6" in out
    assert "NEW      acme-y · acme (custom model)   [Sheet1 row 5]" in out
    assert "SKIPPED  [Sheet1 row 9]\n         batch prices aren't supported" in out
    assert f"save {sheet} --expected-version 6" in out
    assert not opik.sent("PUT", PRICES), "a preview must only ever send a dry run"


def test_a_backend_error_points_at_the_sheet_row_and_nothing_can_be_saved(
    opik: StubOpik, run: RunScript, tmp_path: Path
) -> None:
    opik.answer("GET", PRICES, 200, SAVED)
    opik.answer(
        "PUT",
        f"{PRICES}?dry_run=true",
        422,
        {
            "errors": [
                {"row": 2, "field": "cache_read_price", "code": "x", "message": "must be > 0"}
            ]
        },
    )
    sheet = _sheet(
        tmp_path,
        {"rows": [{"ref": "B7", "model": "acme-y", "provider": "acme", "cache_read": 0}]},
    )

    code, out = run("preview", sheet)

    assert code == model_prices.EXIT_INVALID, out
    assert "ERROR    acme-y · acme   [B7]\n         must be > 0" in out


def test_rows_that_are_wrong_before_sending_never_reach_opik(
    opik: StubOpik, run: RunScript, tmp_path: Path
) -> None:
    opik.answer("GET", PRICES, 200, SAVED)
    sheet = _sheet(
        tmp_path,
        {
            "rows": [
                {"ref": "r1", "model": "acme-y", "provider": "acme", "input": "1.5"},
                {"ref": "r2", "model": "ACME-Y", "provider": "acme", "input": "2"},
                {"ref": "r3", "model": "acme-z", "provider": "acme", "input": "€3"},
            ]
        },
    )

    code, out = run("preview", sheet)

    assert code == model_prices.EXIT_INVALID, out
    assert "the same model and provider as r1" in out
    assert "'€3' is not a number of dollars" in out
    assert not opik.sent("PUT", f"{PRICES}?dry_run=true")


def test_save_refuses_when_the_list_moved_since_the_preview(
    opik: StubOpik, run: RunScript, tmp_path: Path
) -> None:
    opik.answer("GET", PRICES, 200, {**SAVED, "version": 7})
    sheet = _sheet(tmp_path, {"rows": [{"model": "gpt-4o", "provider": "openai", "output": 6}]})

    code, out = run("save", sheet, "--expected-version", "6")

    assert code == model_prices.EXIT_CONFLICT, out
    assert "version 7, not 6" in out
    assert not opik.sent("PUT", PRICES)


def test_a_save_that_loses_the_race_is_a_conflict(
    opik: StubOpik, run: RunScript, tmp_path: Path
) -> None:
    opik.answer("GET", PRICES, 200, SAVED)
    opik.answer("PUT", PRICES, 409, {"errors": ["The list changed"]})
    sheet = _sheet(tmp_path, {"rows": [{"model": "gpt-4o", "provider": "openai", "output": 6}]})

    code, out = run("save", sheet, "--expected-version", "6")

    assert code == model_prices.EXIT_CONFLICT, out
    assert "Run preview again" in out


def test_a_save_is_read_back_and_reports_what_new_spans_cost(
    opik: StubOpik, run: RunScript, tmp_path: Path
) -> None:
    after = {**GPT_4O, "output_price": "6"}
    opik.answer("GET", PRICES, 200, SAVED)
    opik.answer("GET", PRICES, 200, {"version": 7, "rows": [after, ACME_X]})
    opik.answer(
        "PUT",
        PRICES,
        200,
        {
            **NOTHING_CHANGED,
            "version": 7,
            "saved": True,
            "updated": [{"before": GPT_4O, "after": after}],
            "unchanged": 1,
        },
    )
    opik.answer(
        "POST",
        f"{PRICES}/resolve",
        200,
        {
            "lines": [
                {"name": "input", "rate_per_million": "2", "source": "organization"},
                {"name": "output", "rate_per_million": "6", "source": "organization"},
                {"name": "cache_read", "rate_per_million": "1.25", "source": "built_in"},
                {"name": "cache_write", "rate_per_million": "0", "source": "none"},
            ]
        },
    )
    sheet = _sheet(tmp_path, {"rows": [{"model": "gpt-4o", "provider": "openai", "output": 6}]})

    code, out = run("save", sheet, "--expected-version", "6")

    assert code == 0, out
    body = opik.sent("PUT", PRICES)[0].body
    assert isinstance(body, dict)
    assert body["expected_version"] == 6
    assert "Saved price list version 7: 1 updated, 0 new, 0 unchanged, 0 skipped." in out
    assert "Checked: all 1 rows read back as sent." in out
    assert "gpt-4o · openai: input 2 (yours) · output 6 (yours) · cache read 1.25 (built-in)" in out


def test_turned_off_prices_stop_with_the_reason(opik: StubOpik, run: RunScript) -> None:
    opik.answer("GET", PRICES, 503, {"code": 503, "message": "Custom model prices are disabled"})

    code, out = run("show")

    assert code == model_prices.EXIT_UNAVAILABLE, out
    assert "TOGGLE_MODEL_PRICES_ENABLED" in out


def test_a_failed_admin_check_asks_to_retry_instead_of_blaming_the_toggle(
    opik: StubOpik, run: RunScript, tmp_path: Path
) -> None:
    opik.answer("GET", PRICES, 200, SAVED)
    opik.answer(
        "PUT",
        f"{PRICES}?dry_run=true",
        503,
        {"code": 503, "message": "Could not check whether you are an organization admin"},
    )
    sheet = _sheet(tmp_path, {"rows": [{"model": "gpt-4o", "provider": "openai", "output": 6}]})

    code, out = run("preview", sheet)

    assert code == model_prices.EXIT_FAILED, out
    assert "Try again" in out
    assert "TOGGLE_MODEL_PRICES_ENABLED" not in out


def test_a_preview_by_a_non_admin_says_who_can_preview(
    opik: StubOpik, run: RunScript, tmp_path: Path
) -> None:
    opik.answer("GET", PRICES, 200, SAVED)
    opik.answer(
        "PUT",
        f"{PRICES}?dry_run=true",
        403,
        {"code": 403, "message": "Only organization admins can change organization settings"},
    )
    sheet = _sheet(tmp_path, {"rows": [{"model": "gpt-4o", "provider": "openai", "output": 6}]})

    code, out = run("preview", sheet)

    assert code == model_prices.EXIT_FORBIDDEN, out
    assert "Only an admin of the workspace's organization can preview or save prices" in out


def test_a_new_row_for_any_provider_is_not_called_a_custom_model(
    opik: StubOpik, run: RunScript, tmp_path: Path
) -> None:
    gemini = {"model": "gemini-2.5-flash", "provider": "", "kind": "fixed", "input_price": "0.25"}
    opik.answer("GET", PRICES, 200, {"version": 3, "rows": []})
    opik.answer(
        "PUT",
        f"{PRICES}?dry_run=true",
        200,
        {**NOTHING_CHANGED, "version": 3, "created": [gemini], "unchanged": 0},
    )
    sheet = _sheet(tmp_path, {"rows": [{"model": "gemini-2.5-flash", "input": "0.25"}]})

    code, out = run("preview", sheet)

    assert code == 0, out
    assert "gemini-2.5-flash · any provider (replaces the built-in price on every provider)" in out


def test_check_says_where_each_span_name_gets_its_price(opik: StubOpik, run: RunScript) -> None:
    gpt_4o_row = {"model": "gpt-4o", "provider": "openai", "kind": "fixed", "output_price": "6"}
    opik.answer(
        "POST",
        f"{PRICES}/resolve",
        200,
        {
            "match": {"provider": "openai", "name_form": "date_stripped", "row": gpt_4o_row},
            "lines": [
                {"name": "input", "rate_per_million": "2.5", "source": "built_in"},
                {"name": "output", "rate_per_million": "6", "source": "organization"},
                {"name": "cache_write", "rate_per_million": "0", "source": "none"},
            ],
        },
    )
    opik.answer(
        "POST",
        f"{PRICES}/resolve",
        200,
        {
            "match": {"provider": "openai", "name_form": "exact"},
            "lines": [{"name": "input", "rate_per_million": "0.1", "source": "built_in"}],
        },
    )
    opik.answer("POST", f"{PRICES}/resolve", 200, {"lines": []})

    code, out = run(
        "check", "gpt-4o-2024-08-06", "gpt-4.1-nano", "GPT-4o (Aug)", "--provider", "openai"
    )

    assert code == 0, out
    assert [call.body for call in opik.sent("POST", f"{PRICES}/resolve")] == [
        {"model": "gpt-4o-2024-08-06", "provider": "openai"},
        {"model": "gpt-4.1-nano", "provider": "openai"},
        {"model": "GPT-4o (Aug)", "provider": "openai"},
    ]
    assert (
        "gpt-4o-2024-08-06 · openai: your row gpt-4o · openai, matched by its name without the date"
        "\n      input 2.5 (built-in) · output 6 (yours)\n" in out
    )
    assert "gpt-4.1-nano · openai: built-in price\n      input 0.1 (built-in)" in out
    assert "GPT-4o (Aug) · openai: NO PRICE" in out


def test_the_api_key_is_sent_but_never_printed(opik: StubOpik, run: RunScript) -> None:
    opik.answer("GET", PRICES, 200, SAVED)

    code, out = run("show")

    assert code == 0, out
    assert opik.calls[0].headers.get("Authorization") == "secret-key-123"
    assert opik.calls[0].headers.get("Comet-Workspace") == "team"
    assert "secret-key-123" not in out
    assert "/team/configuration?tab=cost-center" in out
