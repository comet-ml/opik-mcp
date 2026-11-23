"""Write ``src/opik_mcp/read_list/record_shapes.json`` from opik's OpenAPI spec."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from collections.abc import Iterable, Mapping
from pathlib import Path

import yaml

from opik_mcp.read_list.registry import ENTITY_REGISTRY

REPOSITORY = "comet-ml/opik"
SPEC_PATH = "apps/opik-documentation/documentation/fern/openapi/opik.yaml"
OUTPUT = Path(__file__).resolve().parents[1] / "src/opik_mcp/read_list/record_shapes.json"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_TIMEOUT_S = 30

FieldShape = dict[str, str | list[str]]
Schemas = dict[str, dict[str, FieldShape]]


class _SpecLoader(yaml.SafeLoader):
    """The spec has ``=`` as a value in an enum, which PyYAML reads as the
    ``tag:yaml.org,2002:value`` tag and refuses without a constructor."""


def _value_as_text(loader: yaml.SafeLoader, node: yaml.Node) -> str:
    if not isinstance(node, yaml.ScalarNode):
        raise yaml.constructor.ConstructorError(None, None, "a '=' value must be a scalar")
    return str(loader.construct_scalar(node))


_SpecLoader.add_constructor("tag:yaml.org,2002:value", _value_as_text)


def _get(url: str, *, accept: str | None = None) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": accept} if accept else {})
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
        body: bytes = response.read()
    return body


def resolve_ref(ref: str) -> str:
    if _SHA.match(ref):
        return ref
    url = f"https://api.github.com/repos/{REPOSITORY}/commits/{ref}"
    return _get(url, accept="application/vnd.github.sha").decode().strip()


def download_spec(sha: str) -> bytes:
    return _get(f"https://raw.githubusercontent.com/{REPOSITORY}/{sha}/{SPEC_PATH}")


def load_spec(raw: bytes) -> object:
    loader = _SpecLoader(raw)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _resolve(node: Mapping[str, object], schemas: Mapping[str, object]) -> Mapping[str, object]:
    all_of = node.get("allOf")
    if isinstance(all_of, list) and len(all_of) == 1:
        node = _mapping(all_of[0])
    ref = node.get("$ref")
    if isinstance(ref, str):
        return _mapping(schemas.get(ref.rsplit("/", 1)[-1]))
    return node


def classify(prop: Mapping[str, object], schemas: Mapping[str, object]) -> FieldShape:
    node = _resolve(prop, schemas)
    if node.get("type") == "array":
        items = _resolve(_mapping(node.get("items")), schemas)
        return {"kind": "named_list" if "name" in _mapping(items.get("properties")) else "list"}
    properties = _mapping(node.get("properties"))
    is_union = "anyOf" in node or "oneOf" in node
    if properties and not is_union:
        return {"kind": "object", "fields": sorted(properties)}
    if is_union or node.get("type") == "object" or "additionalProperties" in node:
        return {"kind": "open"}
    enum = node.get("enum")
    if isinstance(enum, list):
        return {"kind": "scalar", "enum": [str(value) for value in enum]}
    return {"kind": "scalar"}


def build(spec: Mapping[str, object], names: Iterable[str]) -> Schemas:
    schemas = _mapping(_mapping(spec.get("components")).get("schemas"))
    out: Schemas = {}
    for name in sorted(set(names)):
        if name not in schemas:
            raise SystemExit(f"The spec has no schema {name!r}; an entity in read_list names it.")
        properties = _mapping(_mapping(schemas[name]).get("properties"))
        out[name] = {
            prop: classify(_mapping(value), schemas) for prop, value in sorted(properties.items())
        }
    return out


def entity_schema_names() -> set[str]:
    names: set[str] = set()
    for handler in ENTITY_REGISTRY.values():
        if handler.list_record_schema is not None:
            names.add(handler.list_record_schema)
        names.update(handler.read_record_schemas.values())
    return names


def changes(
    old: Mapping[str, Mapping[str, object]], new: Mapping[str, Mapping[str, object]]
) -> list[str]:
    lines: list[str] = []
    for name in sorted(set(old) | set(new)):
        before, after = _mapping(old.get(name)), _mapping(new.get(name))
        added = sorted(set(after) - set(before))
        removed = sorted(set(before) - set(after))
        changed = sorted(f for f in set(after) & set(before) if after[f] != before[f])
        parts = [
            f"{label} {', '.join(fields)}"
            for label, fields in (("added", added), ("removed", removed), ("changed", changed))
            if fields
        ]
        if parts:
            lines.append(f"- `{name}`: {'; '.join(parts)}")
    return lines


def render(source: Mapping[str, str], schemas: Schemas) -> str:
    lines = ["{", f' "source": {json.dumps(source, sort_keys=True)},', ' "schemas": {']
    for index, (name, fields) in enumerate(sorted(schemas.items())):
        lines.append(f"  {json.dumps(name)}: {{")
        rows = [
            f"   {json.dumps(field)}: {json.dumps(shape, sort_keys=True)}"
            for field, shape in sorted(fields.items())
        ]
        lines.append(",\n".join(rows))
        lines.append("  }" + ("," if index < len(schemas) - 1 else ""))
    lines += [" }", "}"]
    return "\n".join(lines) + "\n"


def write_if_changed(output: Path, source: Mapping[str, str], schemas: Schemas) -> list[str]:
    old = json.loads(output.read_text()).get("schemas", {}) if output.exists() else {}
    lines = changes(old, schemas)
    if lines or not output.exists():
        output.write_text(render(source, schemas))
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--ref", default="main", help="opik branch, tag or commit SHA")
    parser.add_argument("--spec", type=Path, help="a local opik.yaml instead of downloading")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)

    if args.spec is not None:
        if not _SHA.match(args.ref):
            parser.error("--spec needs --ref <commit sha>, the commit the file comes from")
        sha, raw = args.ref, args.spec.read_bytes()
    else:
        sha = resolve_ref(args.ref)
        raw = download_spec(sha)
    spec = _mapping(load_spec(raw))
    schemas = build(spec, entity_schema_names())

    source = {
        "repository": REPOSITORY,
        "path": SPEC_PATH,
        "commit": sha,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    lines = write_if_changed(args.output, source, schemas)
    print("\n".join(lines) if lines else "No field changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
