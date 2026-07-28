"""``opik-mcp skills`` — install the bundled skills onto disk (OPIK-7592).

The skills served over MCP by ``read_skill`` also ship inside the wheel (see
``skills/``). This subcommand copies them to a local skills directory so a
coding agent can use them **without** connecting the MCP — the non-MCP front
door, in code form. No server, no auth: a plain file copy from the installed
package.

Usage::

    opik-mcp skills list
    opik-mcp skills install [--dir DIR] [--force] [SKILL ...]

``--dir`` defaults to ``.claude/skills`` under the current directory. Pass
specific skill names to install a subset; omit them to install all.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from importlib.resources import as_file, files
from importlib.resources.abc import Traversable
from pathlib import Path

_DEFAULT_DIR = Path(".claude") / "skills"

# Block scalar indicators a YAML frontmatter ``description:`` may use instead of
# an inline value ("description: >"); when seen, the real text is on the
# following indented lines.
_BLOCK_SCALARS = frozenset({"", ">", "|", ">-", "|-", ">+", "|+"})


def _skills_root() -> Traversable:
    return files("opik_mcp") / "skills"


def _available() -> list[str]:
    """Skill names = immediate sub-directories of ``skills/`` (skips files)."""
    return sorted(p.name for p in _skills_root().iterdir() if p.is_dir())


def _describe(name: str) -> str:
    """First line of a skill's ``description:`` frontmatter, best-effort."""
    try:
        text = (_skills_root() / name / "SKILL.md").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("description:"):
            continue
        value = stripped[len("description:") :].strip()
        if value not in _BLOCK_SCALARS:
            return value.strip('"').strip("'")
        # Folded/literal block scalar — gather the following indented lines.
        collected: list[str] = []
        for nxt in lines[i + 1 :]:
            if nxt.strip() == "" or not nxt.startswith((" ", "\t")):
                break
            collected.append(nxt.strip())
            if sum(len(c) for c in collected) > 90:
                break
        return " ".join(collected)
    return ""


def _cmd_list() -> int:
    for name in _available():
        desc = _describe(name)
        print(f"{name:<14}  {desc[:88]}" if desc else name)
    return 0


def _cmd_install(dest: Path, only: list[str], force: bool) -> int:
    available = _available()
    picks = only or available
    unknown = [s for s in picks if s not in available]
    if unknown:
        print(f"unknown skill(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"available: {', '.join(available)}", file=sys.stderr)
        return 2

    dest.mkdir(parents=True, exist_ok=True)
    written = 0
    with as_file(_skills_root()) as root:  # concrete path even from a zip import
        for name in picks:
            target = dest / name
            if target.exists() and not force:
                print(f"skip {name} — already exists (use --force to overwrite)")
                continue
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(Path(root) / name, target)
            print(f"installed {name} -> {target}")
            written += 1
    print(f"\nDone. {written} skill(s) written to {dest}.")
    return 0


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="opik-mcp skills",
        description="Install the Opik coding-agent skills onto disk (no MCP required).",
    )
    sub = parser.add_subparsers(dest="action")
    sub.add_parser("list", help="list the bundled skills and their descriptions")
    inst = sub.add_parser("install", help="copy skills into a local directory")
    inst.add_argument(
        "--dir",
        type=Path,
        default=_DEFAULT_DIR,
        help="target skills directory (default: .claude/skills)",
    )
    inst.add_argument(
        "--force", action="store_true", help="overwrite existing skill directories"
    )
    inst.add_argument(
        "skills", nargs="*", help="specific skills to install (default: all)"
    )

    args = parser.parse_args(argv)
    if args.action == "list":
        return _cmd_list()
    if args.action == "install":
        return _cmd_install(args.dir, args.skills, args.force)
    parser.print_help()
    return 1
