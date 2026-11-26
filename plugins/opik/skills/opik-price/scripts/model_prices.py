#!/usr/bin/env python3
from __future__ import annotations

import argparse
import configparser
import importlib
import json
import os
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

Json = dict[str, object]
Key = tuple[str, str]

CLOUD_URL = "https://www.comet.com/opik/api/"
PRICES_PATH = "/v1/private/model-prices"
FIELDS = ("input_price", "output_price", "cache_read_price", "cache_write_price")
FIELD_BY_SHEET_NAME = {
    "input": "input_price",
    "output": "output_price",
    "cache_read": "cache_read_price",
    "cache_write": "cache_write_price",
    **{name: name for name in FIELDS},
}
LABELS = {
    "input_price": "input",
    "output_price": "output",
    "cache_read_price": "cache read",
    "cache_write_price": "cache write",
}
LINE_LABELS = {
    "input": "input",
    "output": "output",
    "cache_read": "cache read",
    "cache_write": "cache write",
}
UNITS = {"per_1m": Decimal(1), "per_1k": Decimal(1000), "per_token": Decimal(1_000_000)}
UNIT_NOTE = "Prices are dollars per 1M tokens."
WHOSE = {"organization": "yours", "built_in": "built-in"}
# Opik answers 503 both when the prices toggle is off and when it can't ask Comet whether the
# caller is an organization admin; only this message means the toggle.
PRICES_DISABLED = "Custom model prices are disabled"
NAME_FORMS = {
    "lower_cased": "its name in lower case",
    "normalized": "its normalized name",
    "date_stripped": "its name without the date",
    "normalized_date_stripped": "its normalized name without the date",
    "version_stripped": "its name without the version",
    "normalized_version_stripped": "its normalized name without the version",
}
READ_ONLY_FIELDS = ("id", "created_at", "created_by", "last_updated_at", "last_updated_by")
# Opik saves these provider spellings under another name (ProviderRegistry in the Opik
# backend), so a sheet row is matched to a saved row under the name Opik saves it as.
PROVIDER_ALIASES = {
    "google_ai": "gemini",
    "google_vertexai": "vertex_ai-language-models",
    "anthropic_vertexai": "vertex_ai-anthropic_models",
    "vertex-ai": "vertex_ai-language-models",
    "moonshotai": "moonshot",
    "z-ai": "zai",
    "together": "together_ai",
}

EXIT_FAILED = 1
EXIT_INVALID = 2
EXIT_CONFLICT = 3
EXIT_FORBIDDEN = 4
EXIT_UNAVAILABLE = 5


def say(text: str = "") -> None:
    sys.stdout.write(f"{text}\n")


class Stop(Exception):
    def __init__(self, message: str, code: int = EXIT_FAILED) -> None:
        super().__init__(message)
        self.code = code


class OpikError(Exception):
    def __init__(self, status: int, body: object) -> None:
        super().__init__(f"Opik answered {status}")
        self.status = status
        self.body = body


def as_dict(value: object) -> Json:
    return value if isinstance(value, dict) else {}


def as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


@dataclass(frozen=True)
class Connection:
    url: str
    workspace: str
    api_key: str | None
    source: str

    def describe(self) -> str:
        key = "API key set" if self.api_key else "no API key"
        return f"Opik {self.url.rstrip('/')} · workspace {self.workspace} · {key} ({self.source})"

    def cost_center_link(self) -> str | None:
        base = self.url.rstrip("/")
        if not base.endswith("/api"):
            return None
        workspace = urllib.parse.quote(self.workspace)
        return f"{base.removesuffix('/api')}/{workspace}/configuration?tab=cost-center"


def connection_from_sdk() -> Connection | None:
    try:
        sdk_config = importlib.import_module("opik.config")
    except ImportError:
        return None
    config = sdk_config.OpikConfig()
    api_key = config.api_key
    return Connection(
        url=str(config.url_override),
        workspace=str(config.workspace or "default"),
        api_key=str(api_key) if api_key else None,
        source="Opik SDK settings",
    )


def connection_from_files() -> Connection:
    path = Path(os.environ.get("OPIK_CONFIG_PATH", "").strip() or "~/.opik.config").expanduser()
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)
    section = dict(parser.items("opik")) if parser.has_section("opik") else {}

    def setting(name: str) -> str | None:
        value = os.environ.get(f"OPIK_{name.upper()}") or section.get(name)
        return value.strip() if value and value.strip() else None

    return Connection(
        url=setting("url_override") or CLOUD_URL,
        workspace=setting("workspace") or "default",
        api_key=setting("api_key"),
        source=f"environment and {path}",
    )


def load_connection() -> Connection:
    return connection_from_sdk() or connection_from_files()


def call(
    connection: Connection, method: str, path: str, body: object = None, *, dry_run: bool = False
) -> object:
    url = connection.url.rstrip("/") + PRICES_PATH + path + ("?dry_run=true" if dry_run else "")
    headers = {"Accept": "application/json", "Comet-Workspace": connection.workspace}
    if connection.api_key:
        headers["Authorization"] = connection.api_key
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raw_error = error.read()
        try:
            parsed: object = json.loads(raw_error)
        except ValueError:
            parsed = raw_error.decode("utf-8", "replace")
        raise OpikError(error.code, parsed) from None
    except urllib.error.URLError as error:
        raise Stop(f"Can't reach Opik at {connection.url}: {error.reason}") from None
    return json.loads(raw) if raw else None


def error_text(body: object) -> str:
    data = as_dict(body)
    messages = [as_str(item) for item in as_list(data.get("errors")) if isinstance(item, str)]
    message = as_str(data.get("message"))
    return "; ".join(messages) or message or (body if isinstance(body, str) else "")


def stop_for(error: OpikError, *, writing: bool = False) -> Stop:
    detail = error_text(error.body)
    if error.status == 503 and detail != PRICES_DISABLED:
        return Stop(
            f"Opik is unavailable for now ({detail or 'no reason given'}). Nothing was saved. "
            "Try again in a minute.",
            EXIT_FAILED,
        )
    if error.status == 503:
        return Stop(
            "Custom model prices are turned off on this Opik. An Opik admin turns them on "
            "(TOGGLE_MODEL_PRICES_ENABLED=true on the backend).",
            EXIT_UNAVAILABLE,
        )
    if error.status == 404:
        return Stop(
            "This Opik has no model prices API. It needs an Opik version with the Cost center.",
            EXIT_UNAVAILABLE,
        )
    if error.status == 401:
        return Stop(
            "Opik refused the API key. Run `opik configure`, or set OPIK_API_KEY.", EXIT_FORBIDDEN
        )
    if error.status == 403:
        who = (
            "Only an admin of the workspace's organization can preview or save prices."
            if writing
            else "Opik refused access."
        )
        return Stop(f"{who} Opik said: {detail}" if detail else who, EXIT_FORBIDDEN)
    if error.status == 409:
        return Stop(
            "The price list changed since it was read: someone saved in between. Nothing was "
            "saved. Run preview again and confirm the new changes.",
            EXIT_CONFLICT,
        )
    return Stop(f"Opik answered {error.status}. {detail}".strip())


def fetch_list(connection: Connection) -> Json:
    try:
        return as_dict(call(connection, "GET", ""))
    except OpikError as error:
        raise stop_for(error) from None


def parse_price(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("is not a number")
    if isinstance(value, float):
        text = repr(value)
    elif isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip().removeprefix("$").strip()
    else:
        raise ValueError("is not a number")
    try:
        price = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"'{value}' is not a number of dollars") from None
    if not price.is_finite():
        raise ValueError(f"'{value}' is not a number of dollars")
    return price


def plain(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def model_key(model: str) -> str:
    name = model.strip()
    slash = name.find("/")
    return (name[slash + 1 :] if slash != -1 else name).strip().lower()


def provider_key(provider: str, model: str) -> str:
    name = provider.strip().lower()
    if not name:
        slash = model.find("/")
        prefix = model[:slash].strip().lower() if slash > 0 else ""
        name = prefix
    return PROVIDER_ALIASES.get(name, name)


def key_of(model: str, provider: str) -> Key:
    return model_key(model), provider_key(provider, model)


def describe(model: str, provider: str) -> str:
    return f"{model} · {provider or 'any provider'}"


def name_of(row: Json) -> str:
    return describe(as_str(row.get("model")), as_str(row.get("provider")))


def row_key(row: Json) -> Key:
    return key_of(as_str(row.get("model")), as_str(row.get("provider")))


@dataclass
class SheetRow:
    ref: str
    model: str = ""
    provider: str = ""
    prices: dict[str, str] = field(default_factory=dict)
    note: str | None = None
    same_as: dict[str, str] | None = None
    skip: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def key(self) -> Key:
        return key_of(self.model, self.provider)

    def label(self) -> str:
        return describe(self.model, self.provider) if self.model else "(no model)"

    def to_api(self) -> Json:
        row: Json = {"model": self.model, "provider": self.provider}
        if self.same_as is not None:
            row["kind"] = "alias"
            row["alias_of"] = dict(self.same_as)
        else:
            row["kind"] = "fixed"
            row.update(self.prices)
        if self.note:
            row["note"] = self.note
        return row


def parse_row(raw: object, position: int, default_unit: str) -> SheetRow:
    data = as_dict(raw)
    row = SheetRow(ref=as_str(data.get("ref")).strip() or f"row {position}")
    if not data:
        row.errors.append("each row must be a JSON object")
        return row
    row.model = as_str(data.get("model")).strip()
    row.provider = as_str(data.get("provider")).strip()
    skip = data.get("skip")
    if skip:
        row.skip = as_str(skip) or "skipped"
        return row
    if not row.model:
        row.errors.append("no model name")
    unit = as_str(data.get("unit")) or default_unit
    if unit not in UNITS:
        row.errors.append(f"unit '{unit}' is not one of {', '.join(UNITS)}")
        return row
    known = {"ref", "model", "provider", "unit", "note", "same_as", "skip", *FIELD_BY_SHEET_NAME}
    unknown = sorted(name for name in data if name not in known)
    if unknown:
        row.errors.append(f"unknown fields: {', '.join(unknown)}")
    for sheet_name, api_field in FIELD_BY_SHEET_NAME.items():
        value = data.get(sheet_name)
        if value is None or value == "":
            continue
        try:
            row.prices[api_field] = plain(parse_price(value) * UNITS[unit])
        except ValueError as error:
            row.errors.append(f"{LABELS[api_field]} {error}")
    note = data.get("note")
    if isinstance(note, str) and note.strip():
        row.note = note.strip()
    same_as = data.get("same_as")
    if same_as is not None:
        target = as_dict(same_as)
        if not as_str(target.get("model")).strip():
            row.errors.append("same_as needs the model it prices like")
        elif row.prices:
            row.errors.append("a row priced like another model can't have its own prices")
        else:
            row.same_as = {
                "model": as_str(target.get("model")).strip(),
                "provider": as_str(target.get("provider")).strip(),
            }
    elif not row.prices and not row.errors:
        row.errors.append("no price in this row")
    return row


def read_rows_file(path: str) -> list[SheetRow]:
    try:
        data: object = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise Stop(f"Can't read {path}: {error.strerror}", EXIT_INVALID) from None
    except ValueError as error:
        raise Stop(f"{path} is not valid JSON: {error}", EXIT_INVALID) from None
    document = as_dict(data)
    rows = as_list(data) if isinstance(data, list) else as_list(document.get("rows"))
    unit = as_str(document.get("unit")) or "per_1m"
    if unit not in UNITS:
        raise Stop(f"unit '{unit}' is not one of {', '.join(UNITS)}", EXIT_INVALID)
    if not rows:
        raise Stop(f"{path} has no rows", EXIT_INVALID)
    return [parse_row(raw, position, unit) for position, raw in enumerate(rows, start=1)]


@dataclass
class Plan:
    version: int
    rows: list[Json]
    sheet_by_index: dict[int, SheetRow]
    sheet_rows: list[SheetRow]

    @property
    def errors(self) -> list[SheetRow]:
        return [row for row in self.sheet_rows if row.errors]


def merge(saved: Json, sheet: SheetRow) -> Json:
    incoming = sheet.to_api()
    incoming["model"] = saved.get("model", sheet.model)
    incoming["provider"] = saved.get("provider", sheet.provider)
    if incoming.get("kind") != saved.get("kind"):
        if "note" not in incoming and "note" in saved:
            incoming["note"] = saved["note"]
        return incoming
    return {**saved, **incoming}


def build_plan(current: Json, sheet_rows: list[SheetRow]) -> Plan:
    rows = [
        {k: v for k, v in as_dict(row).items() if k not in READ_ONLY_FIELDS}
        for row in as_list(current.get("rows"))
    ]
    index_by_key = {row_key(row): index for index, row in enumerate(rows)}
    first_by_key: dict[Key, SheetRow] = {}
    sheet_by_index: dict[int, SheetRow] = {}
    for sheet in sheet_rows:
        if sheet.skip or sheet.errors:
            continue
        earlier = first_by_key.get(sheet.key)
        if earlier is not None:
            sheet.errors.append(f"the same model and provider as {earlier.ref}")
            continue
        first_by_key[sheet.key] = sheet
        index = index_by_key.get(sheet.key)
        if index is None:
            rows.append(sheet.to_api())
            index = len(rows) - 1
        else:
            rows[index] = merge(rows[index], sheet)
        sheet_by_index[index] = sheet
    return Plan(as_int(current.get("version")), rows, sheet_by_index, sheet_rows)


def send(connection: Connection, plan: Plan, *, dry_run: bool) -> Json:
    try:
        return as_dict(
            call(
                connection,
                "PUT",
                "",
                {"rows": plan.rows, "expected_version": plan.version},
                dry_run=dry_run,
            )
        )
    except OpikError as error:
        if error.status == 422:
            attach_problems(plan, as_list(as_dict(error.body).get("errors")))
            return {}
        raise stop_for(error, writing=True) from None


def attach_problems(plan: Plan, problems: list[object]) -> None:
    for raw in problems:
        problem = as_dict(raw)
        index = problem.get("row")
        message = as_str(problem.get("message")) or as_str(problem.get("code")) or as_str(raw)
        sheet = plan.sheet_by_index.get(index) if isinstance(index, int) else None
        if sheet is not None:
            sheet.errors.append(message)
            continue
        where = "the list" if not isinstance(index, int) else _saved_row_label(plan, index)
        orphan = SheetRow(ref=where, errors=[message])
        plan.sheet_rows.append(orphan)


def _saved_row_label(plan: Plan, index: int) -> str:
    if 0 <= index < len(plan.rows):
        row = plan.rows[index]
        return f"saved row {name_of(row)}"
    return f"row {index}"


def resolve(connection: Connection, model: str, provider: str) -> Json:
    return as_dict(call(connection, "POST", "/resolve", {"model": model, "provider": provider}))


def resolve_rates(connection: Connection, model: str, provider: str) -> list[Json]:
    try:
        answer = resolve(connection, model, provider)
    except (OpikError, Stop):
        return []
    return [as_dict(line) for line in as_list(answer.get("lines"))]


def rates_of(lines: list[Json]) -> str:
    return " · ".join(
        f"{LINE_LABELS.get(name, name)} {as_str(line.get('rate_per_million'))} ({whose})"
        for line in lines
        for name in [as_str(line.get("name"))]
        for whose in [WHOSE.get(as_str(line.get("source")), "")]
        if whose
    )


def changes_between(before: Json, after: Json) -> str:
    parts = []
    for name in FIELDS:
        old, new = as_str(before.get(name)), as_str(after.get(name))
        if old != new:
            parts.append(f"{LABELS[name]} {old or '—'} → {new or '—'}")
    if before.get("kind") != after.get("kind"):
        parts.append(f"kind {as_str(before.get('kind'))} → {as_str(after.get('kind'))}")
    if as_dict(before.get("alias_of")) != as_dict(after.get("alias_of")):
        parts.append(f"priced like {name_of(as_dict(after.get('alias_of')))}")
    if as_str(before.get("note")) != as_str(after.get("note")):
        parts.append("note changed")
    return " · ".join(parts)


def prices_of(row: Json) -> str:
    if row.get("kind") == "alias":
        return f"priced like {name_of(as_dict(row.get('alias_of')))}"
    return " · ".join(
        f"{LABELS[name]} {as_str(row[name])}" for name in FIELDS if as_str(row.get(name))
    )


def over_built_in(connection: Connection, row: Json) -> str:
    lines = resolve_rates(connection, as_str(row.get("model")), as_str(row.get("provider")))
    rates = {as_str(line.get("name")): line for line in lines}
    parts = []
    for name in FIELDS:
        new = as_str(row.get(name))
        if not new:
            continue
        line = rates.get(name.removesuffix("_price"), {})
        old = as_str(line.get("rate_per_million")) if line.get("source") == "built_in" else ""
        parts.append(f"{LABELS[name]} {old} → {new}" if old else f"{LABELS[name]} {new}")
    return " · ".join(parts)


@dataclass
class Outcome:
    updated: int = 0
    added: int = 0
    unchanged: int = 0
    lines: list[str] = field(default_factory=list)


def classify(connection: Connection, plan: Plan, result: Json) -> Outcome:
    created = {row_key(row): row for row in map(as_dict, as_list(result.get("created")))}
    updated = {
        row_key(as_dict(change.get("after"))): (
            as_dict(change.get("before")),
            as_dict(change.get("after")),
        )
        for change in map(as_dict, as_list(result.get("updated")))
    }
    custom = {
        index
        for warning in map(as_dict, as_list(result.get("warnings")))
        if warning.get("code") == "no_base"
        for index in [warning.get("row")]
        if isinstance(index, int)
    }
    outcome = Outcome()
    for index, sheet in plan.sheet_by_index.items():
        key = row_key(plan.rows[index])
        if key in updated:
            before, after = updated[key]
            outcome.updated += 1
            outcome.lines.append(
                entry("UPDATE", sheet.label(), sheet.ref, changes_between(before, after))
            )
        elif key in created:
            row = created[key]
            outcome.added += 1
            if row.get("kind") == "alias":
                outcome.lines.append(entry("NEW", name_of(row), sheet.ref, prices_of(row)))
            elif index in custom:
                label = f"{name_of(row)} (custom model)"
                outcome.lines.append(entry("NEW", label, sheet.ref, prices_of(row)))
            elif not as_str(row.get("provider")):
                label = f"{name_of(row)} (replaces the built-in price on every provider)"
                outcome.lines.append(entry("NEW", label, sheet.ref, prices_of(row)))
            else:
                label = f"{name_of(row)} (replaces the built-in price)"
                outcome.lines.append(entry("NEW", label, sheet.ref, over_built_in(connection, row)))
        else:
            outcome.unchanged += 1
            outcome.lines.append(entry("SAME", sheet.label(), sheet.ref))
    return outcome


def entry(status: str, label: str, ref: str, detail: str = "") -> str:
    head = f"{status:<8} {label}   [{ref}]" if label else f"{status:<8} [{ref}]"
    return f"{head}\n         {detail}" if detail else head


def warnings_for(plan: Plan, result: Json) -> list[str]:
    lines = []
    for warning in map(as_dict, as_list(result.get("warnings"))):
        index = warning.get("row")
        sheet = plan.sheet_by_index.get(index) if isinstance(index, int) else None
        if isinstance(index, int) and sheet is None:
            continue
        where = f"[{sheet.ref}] {sheet.label()}: " if sheet else ""
        lines.append(f"  {where}{as_str(warning.get('message'))} ({as_str(warning.get('code'))})")
    return lines


def problem_lines(plan: Plan) -> list[str]:
    return [
        entry("ERROR", sheet.label(), sheet.ref, "; ".join(sheet.errors))
        for sheet in plan.sheet_rows
        if sheet.errors
    ]


def skipped_lines(plan: Plan) -> list[str]:
    return [
        entry("SKIPPED", sheet.label() if sheet.model else "", sheet.ref, sheet.skip)
        for sheet in plan.sheet_rows
        if sheet.skip
    ]


def totals(outcome: Outcome, plan: Plan) -> str:
    errors = len(plan.errors)
    skipped = sum(1 for sheet in plan.sheet_rows if sheet.skip)
    return (
        f"Totals: {outcome.updated} updated, {outcome.added} new, {outcome.unchanged} unchanged, "
        f"{errors} with errors, {skipped} skipped."
    )


def print_problems_and_stop(plan: Plan) -> None:
    say("\n".join(problem_lines(plan) + skipped_lines(plan)))
    say()
    say(totals(Outcome(), plan))
    if not plan.errors:
        say("Nothing to save: every row is skipped.")
        raise Stop("", 0)
    say("Nothing is saved until every error is fixed, or its row is marked skip. Preview again.")
    raise Stop("", EXIT_INVALID)


def preview(connection: Connection, path: str) -> Plan:
    sheet_rows = read_rows_file(path)
    current = fetch_list(connection)
    plan = build_plan(current, sheet_rows)
    say(connection.describe())
    saved_rows = len(as_list(current.get("rows")))
    say(f"Price list version {plan.version}, {saved_rows} saved rows. {UNIT_NOTE}")
    say("Preview only: nothing is saved.\n")
    if plan.errors or not plan.sheet_by_index:
        print_problems_and_stop(plan)
    result = send(connection, plan, dry_run=True)
    if plan.errors or not result:
        print_problems_and_stop(plan)
    if as_list(result.get("deleted")):
        raise Stop(
            "The preview would delete saved rows, which an upload never does. Nothing was saved.",
            EXIT_FAILED,
        )
    outcome = classify(connection, plan, result)
    say("\n".join(outcome.lines + skipped_lines(plan)))
    warnings = warnings_for(plan, result)
    if warnings:
        say("\nWarnings (the rows save, but check them):")
        say("\n".join(warnings))
    say("")
    say(totals(outcome, plan))
    if outcome.updated or outcome.added:
        say("To save after the user confirms:")
        command = ["python3", str(Path(__file__).resolve()), "save", str(Path(path).resolve())]
        say(f"  {shlex.join(command)} --expected-version {plan.version}")
    else:
        say("Nothing to save: every row already matches the saved prices.")
    return plan


def verify(connection: Connection, plan: Plan) -> list[str]:
    saved = fetch_list(connection)
    by_key = {row_key(row): row for row in map(as_dict, as_list(saved.get("rows")))}
    mismatches = []
    for index, sheet in plan.sheet_by_index.items():
        stored = by_key.get(row_key(plan.rows[index]))
        if stored is None:
            mismatches.append(f"{sheet.label()} [{sheet.ref}] is not in the saved list")
            continue
        mismatches.extend(
            f"{sheet.label()} [{sheet.ref}] {LABELS[name]}: saved "
            f"{as_str(stored.get(name)) or '—'}, sent {sent}"
            for name, sent in sheet.prices.items()
            if Decimal(as_str(stored.get(name)) or "NaN") != Decimal(sent)
        )
    return mismatches


def effective_rates(connection: Connection, plan: Plan) -> list[str]:
    lines = []
    for index in plan.sheet_by_index:
        row = plan.rows[index]
        rates = rates_of(
            resolve_rates(connection, as_str(row.get("model")), as_str(row.get("provider")))
        )
        if rates:
            lines.append(f"  {name_of(row)}: {rates}")
    return lines


def save(connection: Connection, path: str, expected_version: int) -> None:
    sheet_rows = read_rows_file(path)
    current = fetch_list(connection)
    if as_int(current.get("version")) != expected_version:
        raise Stop(
            f"The price list is at version {as_int(current.get('version'))}, not "
            f"{expected_version} as previewed: someone saved in between. Nothing was saved. "
            "Run preview again and confirm the new changes.",
            EXIT_CONFLICT,
        )
    plan = build_plan(current, sheet_rows)
    if plan.errors or not plan.sheet_by_index:
        print_problems_and_stop(plan)
    say(connection.describe())
    result = send(connection, plan, dry_run=False)
    if plan.errors or not result:
        print_problems_and_stop(plan)
    if not result.get("saved"):
        say("Nothing saved: every row already matches the saved prices.")
        return
    outcome = classify(connection, plan, result)
    skipped = sum(1 for sheet in plan.sheet_rows if sheet.skip)
    say(
        f"Saved price list version {as_int(result.get('version'))}: {outcome.updated} updated, "
        f"{outcome.added} new, {outcome.unchanged} unchanged, {skipped} skipped."
    )
    mismatches = verify(connection, plan)
    if mismatches:
        say("Check failed. The saved list differs from what was sent:")
        say("\n".join(f"  {line}" for line in mismatches))
        raise Stop("", EXIT_FAILED)
    say(f"Checked: all {len(plan.sheet_by_index)} rows read back as sent.")
    rates = effective_rates(connection, plan)
    if rates:
        say("New spans of these models are now priced at (dollars per 1M tokens):")
        say("\n".join(rates))
    say("Spans logged before keep their cost. Every Opik server uses the new prices within 60 s.")
    link = connection.cost_center_link()
    if link:
        say(f"Cost center: {link}")


def show(connection: Connection) -> None:
    current = fetch_list(connection)
    rows = [as_dict(row) for row in as_list(current.get("rows"))]
    say(connection.describe())
    say(f"Price list version {as_int(current.get('version'))}, {len(rows)} rows. {UNIT_NOTE}")
    for row in rows:
        note = f'   note: "{as_str(row.get("note"))}"' if as_str(row.get("note")) else ""
        say(f"  {name_of(row)}: {prices_of(row)}{note}")
    link = connection.cost_center_link()
    if link:
        say(f"Cost center: {link}")


def how_priced(answer: Json, provider: str) -> str:
    match = as_dict(answer.get("match"))
    if not match:
        return "NO PRICE: spans with this model and provider cost $0 now"
    form = NAME_FORMS.get(as_str(match.get("name_form")), "")
    via = f", matched by {form}" if form else ""
    row = as_dict(match.get("row"))
    if not row:
        filed_under = as_str(match.get("provider"))
        where = f" under {filed_under}" if filed_under != provider.strip().lower() else ""
        return f"built-in price{where}{via}"
    if row.get("kind") == "alias":
        return f"your row {name_of(row)}, {prices_of(row)}{via}"
    return f"your row {name_of(row)}{via}"


def check(connection: Connection, models: list[str], provider: str) -> None:
    if any(not model.strip() for model in models):
        raise Stop("Give each model a name.", EXIT_INVALID)
    say(connection.describe())
    say(f"How spans are priced now. {UNIT_NOTE}")
    for model in models:
        try:
            answer = resolve(connection, model, provider)
        except OpikError as error:
            raise stop_for(error) from None
        rates = rates_of([as_dict(line) for line in as_list(answer.get("lines"))])
        span = f"{model} · {provider or 'no provider'}"
        say(f"  {span}: {how_priced(answer, provider)}")
        if rates:
            say(f"      {rates}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"Read and update the model prices of an Opik organization. {UNIT_NOTE}"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show", help="the saved price list and its version")
    check_parser = commands.add_parser(
        "check", help="how spans with these model names and this provider are priced now"
    )
    check_parser.add_argument("models", nargs="+", metavar="model")
    check_parser.add_argument(
        "--provider", default="", help="the provider the spans send; leave out for none"
    )
    preview_parser = commands.add_parser(
        "preview", help="what a rows file would change; saves nothing"
    )
    preview_parser.add_argument("rows_file")
    save_parser = commands.add_parser(
        "save", help="save a rows file, only at the previewed version"
    )
    save_parser.add_argument("rows_file")
    save_parser.add_argument("--expected-version", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        connection = load_connection()
        if args.command == "show":
            show(connection)
        elif args.command == "check":
            check(connection, args.models, args.provider)
        elif args.command == "preview":
            preview(connection, args.rows_file)
        else:
            save(connection, args.rows_file, args.expected_version)
    except Stop as stop:
        if str(stop):
            sys.stderr.write(f"{stop}\n")
        return stop.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
