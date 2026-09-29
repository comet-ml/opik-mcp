"""Backend payloads, one JSON file each, as opik-backend sends them.

``load("trace", trace_id=…)`` reads ``trace.json`` and fills its placeholders.
The substitution is the whole template language:

- a string that is exactly ``"${name}"`` becomes the value passed for
  ``name``, whatever its type, so a fixture can take a number, a list or
  ``null`` where the backend sends one;
- ``${name}`` inside a longer string becomes ``str(value)``;
- a placeholder with no value passed is an error naming the fixture.

Nothing else is interpreted. A payload computed from other data is built in
Python by whoever serves it, and passed in whole.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_DIR = Path(__file__).parent
_PLACEHOLDER = re.compile(r"\$\{(\w+)\}")


class MissingValueError(KeyError):
    """A fixture names a placeholder the caller gave no value for."""


def load(name: str, /, **values: object) -> object:
    """The payload in ``<name>.json``, placeholders filled from ``values``."""
    parsed: object = json.loads((_DIR / f"{name}.json").read_text())
    return _fill(parsed, name, values)


def record(name: str, /, **values: object) -> dict[str, object]:
    """``load`` for a fixture whose payload is one JSON object."""
    payload = load(name, **values)
    assert isinstance(payload, dict), f"tests/hermetic/fixtures/{name}.json is not an object"
    return {str(key): value for key, value in payload.items()}


def _fill(node: object, name: str, values: dict[str, object]) -> object:
    if isinstance(node, dict):
        return {key: _fill(value, name, values) for key, value in node.items()}
    if isinstance(node, list):
        return [_fill(value, name, values) for value in node]
    if not isinstance(node, str) or "${" not in node:
        return node
    whole = _PLACEHOLDER.fullmatch(node)
    if whole is not None:
        return _value(whole.group(1), name, values)
    return _PLACEHOLDER.sub(lambda match: str(_value(match.group(1), name, values)), node)


def _value(key: str, name: str, values: dict[str, object]) -> object:
    if key not in values:
        raise MissingValueError(f"tests/hermetic/fixtures/{name}.json needs a value for ${{{key}}}")
    return values[key]
