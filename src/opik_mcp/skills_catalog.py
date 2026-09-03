"""Read the skills bundled in the wheel, for serving over MCP (OPIK-7472).

`src/opik_mcp/skills/` is the canonical home of the Opik agent skills (OPIK-7471).
This module is the only runtime reader of that tree: it enumerates the publishable
files, maps each to an `opik://skills/...` URI, and reads one back. Both MCP entry
points — the `resources/*` handlers in `skills_resources` and the `read_skill`
tool in `server` — go through here, so "what the MCP serves" has one definition.

Three properties the tests pin:

*Verbatim.* Content is returned byte for byte as packaged. The pack published to
`comet-ml/opik-skills` copies the same bytes, so a skill read over MCP and the same
skill installed from the pack are the same document — no normalisation on either path.

*Deterministic.* `iter_skill_files()` returns a stable, sorted tuple. The MCP spec's
`resources/list` contract expects a deterministic listing, and hosts cache it.

*Traversal-proof by construction.* A URI is resolved by looking it up in the
enumerated set, never by joining caller-supplied path segments onto a directory.
There is no string to sanitise, so there is no sanitiser to get wrong.

Files are read through `importlib.resources`, not `__file__` arithmetic, so this
works from a zip import as well as a source checkout.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import ClassVar

from opik_mcp.error_kinds import ErrorKind

#: URI prefix for every skill file served over MCP. Reuses the `opik://` scheme
#: the read tool already parses (see `read_list/uri.py`) rather than inventing a
#: second one; `skills/` is not an Opik entity collection, so the two never collide.
SKILLS_URI_PREFIX = "opik://skills/"

#: Directory names never served, mirroring the published pack's exclusions.
#: `evals/` holds fixture repositories and harnesses no agent reads (tens of KB
#: per skill); `__pycache__` is build residue. `scripts/build_skills_pack.py`
#: imports this so the two distribution paths cannot drift apart.
EXCLUDED_DIRS = frozenset({"evals", "__pycache__"})

#: Cache metadata advertised on `resources/list` / `resources/read` and on the
#: `read_skill` tool result (MCP 2026-07-28 `CacheableResult`). Skill content is
#: static for the life of an `opik-mcp` build, so a day is comfortably safe: a
#: new build means a new process, and a host's stale copy expires on its own.
SKILLS_TTL_MS = 86_400_000

#: `public`: skill content is identical for every caller and carries no workspace
#: data, so a host may share one cached copy across sessions and users.
SKILLS_CACHE_SCOPE = "public"

#: One line per skill, keyed on the KIND OF TASK the user gave — because that is
#: the decision the calling agent is making: it has a request in hand and has to
#: pick the skill that request belongs to.
#:
#: Most lines quote the task phrasings from that skill's own authored frontmatter
#: `description` (its "Use for …" list), so the two surfaces mostly route on the
#: same words. This copy is free to diverge where the frontmatter's phrasing is not
#: what a user would actually say — `opik` is the case in point: its frontmatter
#: lists "what span types exist", a question nobody asks out loud, so here it is
#: keyed on real ones ("why aren't my traces showing up"). These lines are MCP-only
#: and do not edit the skills; a phrasing improvement worth having on installed
#: hosts too belongs in that skill's frontmatter, separately.
#:
#: Quoted task phrasings do the disambiguation for free: "which traces need
#: attention" against "why did this trace fail" separates diagnose from explain
#: more sharply than any prose gloss, and costs nothing extra. The one redirect
#: kept is `opik` → `opik-instrument`, since an SDK question and "go add tracing"
#: are easy to confuse and the wrong pick wastes a multi-KB fetch.
#:
#: This is the short form deliberately: the frontmatter is written for a host that
#: has already installed the skill and can afford a paragraph, while every word
#: here is charged to every session's context.
SKILL_SUMMARIES: dict[str, str] = {
    "opik": (
        '"why aren\'t my traces showing up", "how do I add metadata to a span", "how '
        'do I version a prompt" — any SDK lookup. To instrument, use opik-instrument.'
    ),
    "opik-instrument": (
        '"instrument my code", "add opik tracing", "add observability", "trace my agent".'
    ),
    "opik-evaluate": (
        "the user wants to measure or improve AI product quality, or asks about evals, "
        "judges, or evaluation metrics."
    ),
    "opik-diagnose": (
        '"what is broken in production", "which traces need attention", "find failing '
        'or slow traces", "triage my agent".'
    ),
    "opik-dashboards": (
        '"chart our p99 latency", "which model costs the most", "build me a dashboard '
        'for this project", "what does this dashboard show".'
    ),
    "opik-explain": (
        '"why did this trace fail", "explain this trace", "debug this trace", "why is '
        'my agent slow or wrong".'
    ),
}

#: Content types by suffix. Markdown is what skills are made of; the rest cover
#: the `scripts/` and `assets/` directories the skills spec blesses, so an author
#: adding one does not have to come back here.
_MIME_TYPES: dict[str, str] = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".json": "application/json",
    ".py": "text/x-python",
    ".ts": "text/x-typescript",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".toml": "application/toml",
}
_DEFAULT_MIME_TYPE = "text/plain"


class UnknownSkillError(ValueError):
    """The requested skill (or file within one) is not bundled in this build.

    Buckets as `validation` for BI: the caller named something that does not
    exist, which is a payload problem, not an outage. Same contract as
    `read_list.uri.InvalidURI`.
    """

    error_kind: ClassVar[ErrorKind] = "validation"
    http_status: ClassVar[int | None] = 400


@dataclass(frozen=True)
class SkillFile:
    """One publishable file inside one skill."""

    skill: str
    """Skill name, which is also its directory name (the installer keys on it)."""

    path: str
    """POSIX path relative to the skill directory, e.g. `references/production.md`."""

    @property
    def uri(self) -> str:
        return f"{SKILLS_URI_PREFIX}{self.skill}/{self.path}"

    @property
    def is_entry_point(self) -> bool:
        return self.path == "SKILL.md"

    @property
    def mime_type(self) -> str:
        suffix = self.path[self.path.rfind(".") :] if "." in self.path else ""
        return _MIME_TYPES.get(suffix.lower(), _DEFAULT_MIME_TYPE)


def _skills_root() -> Traversable:
    return files("opik_mcp") / "skills"


def _is_publishable(name: str) -> bool:
    """Exclude by rule, not by allow-list — an author's new `scripts/` or
    `assets/` directory is served without anyone having to remember this file."""
    return name not in EXCLUDED_DIRS and not name.startswith(".")


def _walk(node: Traversable, prefix: tuple[str, ...]) -> list[tuple[str, ...]]:
    found: list[tuple[str, ...]] = []
    for child in node.iterdir():
        if not _is_publishable(child.name):
            continue
        if child.is_dir():
            found.extend(_walk(child, (*prefix, child.name)))
        elif child.is_file():
            found.append((*prefix, child.name))
    return found


@cache
def iter_skill_files() -> tuple[SkillFile, ...]:
    """Every publishable file across every bundled skill, deterministically ordered.

    Ordering is (skill name, entry point first, then path) — `SKILL.md` leads its
    skill because it is the document an agent reads first, and the published pack's
    manifest orders it the same way.

    A directory without a `SKILL.md` is not a skill: scratch and shared-note
    directories must not become one by sitting in the right place.

    Cached: the tree is immutable for the life of the process, and `resources/list`
    is called on every session by every host.
    """
    root = _skills_root()
    if not root.is_dir():  # pragma: no cover — a broken wheel, not a code path
        return ()
    entries: list[SkillFile] = []
    for skill_dir in root.iterdir():
        if not skill_dir.is_dir() or not _is_publishable(skill_dir.name):
            continue
        if not (skill_dir / "SKILL.md").is_file():
            continue
        for parts in _walk(skill_dir, ()):
            entries.append(SkillFile(skill=skill_dir.name, path="/".join(parts)))
    entries.sort(key=lambda e: (e.skill, not e.is_entry_point, e.path))
    return tuple(entries)


@cache
def skill_names() -> tuple[str, ...]:
    """Bundled skill names, sorted."""
    return tuple(sorted({f.skill for f in iter_skill_files()}))


@cache
def _by_uri() -> dict[str, SkillFile]:
    return {f.uri: f for f in iter_skill_files()}


def read_skill_file(entry: SkillFile) -> str:
    """The file's content, verbatim.

    Re-checks the entry against the enumerated set before touching the disk. Every
    caller today receives its entry from `resolve_uri` / `resolve_request`, which
    can only return enumerated files — but this function does join path segments,
    so it is the one place where a hand-built `SkillFile` could read outside the
    tree. Keeping the check here makes the guarantee local instead of an obligation
    on every present and future caller.

    Strict UTF-8: a decode error means a binary file reached a text-only
    transport, which is a packaging bug worth failing on rather than serving
    replacement characters into an agent's context.
    """
    if _by_uri().get(entry.uri) != entry:
        raise UnknownSkillError(f"{entry.uri} is not a bundled skill file")
    node = _skills_root() / entry.skill
    for part in entry.path.split("/"):
        node = node / part
    return node.read_text(encoding="utf-8")


def resolve_uri(uri: str) -> SkillFile | None:
    """The file an `opik://skills/...` URI names, or None if it names nothing.

    A lookup in the enumerated set — never a path join — so `..` and absolute
    paths cannot escape the skills tree. They simply miss.
    """
    return _by_uri().get(uri)


def _references(skill: str) -> tuple[SkillFile, ...]:
    return tuple(f for f in iter_skill_files() if f.skill == skill and not f.is_entry_point)


def _reference_names(skill: str) -> tuple[str, ...]:
    """A skill's reference documents by bare name — `tracing-python`, not a path.

    Private, and used for one thing: `resolve` accepts `opik/tracing-python` as
    well as the advertised `opik/references/tracing-python.md`, because the
    directory and the suffix are noise a caller may reasonably drop. The tool
    advertises paths, so this is a tolerance, not a second documented form.
    """
    return tuple(
        f.path.removeprefix("references/").removesuffix(".md")
        for f in _references(skill)
        if f.path.endswith(".md")
    )


def _name_list() -> str:
    return ", ".join(skill_names())


def request_shape(skill_name: str) -> str:
    """Which of the documented argument forms a caller used: `name`, `path`, or `uri`.

    A BI label, and low-cardinality by construction — it never returns any part of
    the caller's string. Shared with `server._read_skill_props` so the classification
    an event carries is the same one resolution acts on.
    """
    requested = skill_name.strip().strip("/")
    if requested.startswith(SKILLS_URI_PREFIX):
        return "uri"
    # `../` is the sibling-reference form a SKILL.md uses, which is a path.
    return "path" if "/" in requested.removeprefix("../") else "name"


def resolve(skill_name: str) -> SkillFile:
    """The file a caller named, in any of the forms the tool documents.

    One argument, four forms, because an agent arrives holding whichever one it
    last saw:

    - `opik` — the skill itself (its `SKILL.md`)
    - `opik/references/tracing-python.md` — a path inside a skill, the form a
      SKILL.md and this tool's own output both use
    - `opik://skills/opik/SKILL.md` — the resource URI, the form `resources/list`
      advertises, so a caller that browsed resources can pass back what it has
    - `../opik/references/integrations.md` — how a SKILL.md cites a *sibling*
      skill's document, quoted back verbatim

    Also accepts a bare reference name (`opik/tracing-python`), since the
    `references/` directory and the `.md` suffix are noise a caller may reasonably
    drop.

    Every form ends in a lookup over the enumerated file set — never a path join —
    so `..` and absolute paths miss rather than escaping the skills tree.

    Raises `UnknownSkillError` naming the valid alternatives: a wrong guess should
    cost one turn, not a fishing expedition.
    """
    requested = skill_name.strip().strip("/")
    while requested.startswith("../"):
        requested = requested[3:]
    if not requested:
        raise UnknownSkillError(f"skill_name is empty; available skills: {_name_list()}")

    # A URI is the same (skill, document) pair wearing a prefix, so strip it and
    # take the one road out. Two roads is what made a bad URI answer with the skill
    # list while the identical mistake in path form answered with the skill's own
    # documents — the same error, recoverable in one form and a dead end in the other.
    as_uri = requested.startswith(SKILLS_URI_PREFIX)
    remainder = requested.removeprefix(SKILLS_URI_PREFIX) if as_uri else requested

    skill, _, relative = remainder.partition("/")
    if skill not in skill_names():
        raise UnknownSkillError(f"unknown skill {skill!r}; available skills: {_name_list()}")

    entry = resolve_uri(f"{SKILLS_URI_PREFIX}{skill}/{relative or 'SKILL.md'}")
    if entry is not None:
        return entry

    # Not an exact path — try it as a bare reference name before giving up.
    wanted = relative.removeprefix("references/").removesuffix(".md")
    if wanted in _reference_names(skill):
        return next(f for f in _references(skill) if f.path.endswith(f"{wanted}.md"))

    raise UnknownSkillError(
        f"{skill!r} has no document {relative!r}; readable: {_readable_list(skill, as_uri)}"
    )


def unknown_skill_uri_message(uri: str) -> str:
    """What to tell a caller whose `opik://skills/...` URI names nothing.

    Used by the resource handler, where the alternative is FastMCP's bare
    "Unknown resource: <uri>" — accurate and useless. A caller that mistypes one
    URI has the whole listing one call away, so the message says so; a caller that
    mistyped the document inside a real skill gets that skill's URIs outright.
    """
    remainder = uri.removeprefix(SKILLS_URI_PREFIX)
    skill = remainder.partition("/")[0]
    if skill in skill_names():
        return f"no skill document at {uri!r}; readable: {_readable_list(skill, as_uri=True)}"
    return (
        f"no skill document at {uri!r}; available skills: {_name_list()} "
        "— call resources/list for every readable URI"
    )


def _readable_list(skill: str, as_uri: bool = False) -> str:
    """A skill's documents, spelled in the form the caller used.

    An agent holding URIs should be handed URIs to retry with, not paths it then
    has to translate back — the translation is exactly the step that costs the
    extra turn this message exists to save.
    """
    prefix = f"{SKILLS_URI_PREFIX}{skill}/" if as_uri else f"{skill}/"
    return ", ".join(f"{prefix}{path}" for path in readable_paths(skill))


def readable_paths(skill: str) -> tuple[str, ...]:
    """Everything readable inside one skill, as the paths the tool accepts."""
    return ("SKILL.md", *(f.path for f in _references(skill)))


def run_read_skill(skill_name: str) -> str:
    """The `read_skill` tool body: one skill document, ready to act on.

    Output is a one-line `[read_skill: …]` header (mirroring the `read` tool's
    shape) followed by the document verbatim, and — for a skill's entry point —
    the paths of its reference documents, because a SKILL.md routinely tells the
    agent to go read one and it would otherwise have to guess the argument.

    Both the header and the footer quote the *resolved* file, not the caller's
    spelling: this output is documentation an agent imitates on its next call.
    """
    entry = resolve(skill_name)
    content = read_skill_file(entry)
    header = (
        f"[read_skill: {entry.skill} path={entry.path} "
        f"bytes={len(content.encode('utf-8'))} uri={entry.uri}]"
    )
    parts = [header, content]
    if entry.is_entry_point:
        references = _references(entry.skill)
        if references:
            listed = "\n".join(f"- {entry.skill}/{f.path}" for f in references)
            parts.append(
                f"References for this skill — pass one as skill_name, e.g. "
                f'read_skill("{entry.skill}/{references[0].path}"):\n{listed}'
            )
    return "\n\n".join(parts)


def read_skill_tool_description() -> str:
    """The `read_skill` tool description, rendered from the bundled skills.

    Rendered rather than hand-written so a skill can never be bundled and left
    unmentioned — both the routing list and the document inventory are generated
    from the same tree the tool serves.

    Frames the tool around what the agent can see rather than around how the user
    installs things: the question at call time is whether the skill is already in
    context, and "go install the pack" is advice for a person, not a step the
    agent can take mid-task. It also spent tokens on every session to say
    something most sessions could not act on.

    The inventory is the expensive part — every path is charged to every session's
    context — and it is here deliberately: a caller that can see
    `opik/references/tracing-python.md` fetches it directly, where a caller that
    cannot has to read the 5 KB `SKILL.md` first to learn the name.
    """
    catalog = "\n".join(
        f"- {name}: {SKILL_SUMMARIES[name]}" for name in skill_names() if name in SKILL_SUMMARIES
    )
    inventory = "\n".join(f"- {name}: {', '.join(readable_paths(name))}" for name in skill_names())
    return (
        "Load one Opik agent skill — the same skills Opik publishes for coding "
        "agents. Use it when the skill you need is not already in your context.\n\n"
        "WHEN TO CALL: before instrumenting, evaluating, or debugging with Opik, "
        "when the relevant skill below is NOT already loaded — no local copy in "
        "the project, and nothing fetched earlier this session. If you already "
        "have it, read what you have instead of fetching it again.\n\n"
        # The instruction lives here, once — repeating "fetch this skill when" on
        # every line would spend five times the tokens saying one thing five times.
        f"Match the user's task to a skill:\n{catalog}\n\n"
        "`skill_name` accepts any of these forms:\n"
        "- `opik` — the skill itself\n"
        "- `opik/references/tracing-python.md` — one document inside a skill\n"
        f"- `{SKILLS_URI_PREFIX}opik/SKILL.md` — the same document by its resource "
        "URI, as listed by resources/list\n\n"
        f"Readable paths, prefixed with the skill name as above:\n{inventory}"
    )
