"""
File: scripts/_gh_project_lib.py

Description:

  Shared data model, plan-file parsing, pure planning logic, and GitHub
  I/O layer for the gh_project_{populate, update, lint} scripts.

  The design is functional-core / imperative-shell:

  - Parsing (`parse_project_plan`, `parse_file`) and planning
    (`plan_labels`, `plan_milestones`, `plan_issues`) are pure functions
    over strings and immutable records.  They never touch the network,
    so `--dry-run` and `make lint` are exact by construction: a dry run
    prints the same plan a real run executes.
  - `GitHubClient` wraps the `gh` CLI in Result-returning methods.  Its
    read side produces one `RepoSnapshot` per run; its write side
    performs plain creations with no hidden existence checks.  The
    existence checks live in the pure planners, which receive the
    snapshot — this is what makes populate O(n) in API calls instead of
    re-fetching the world per creation (ualib/agda-algebras#293 item 6).

  All GitHubClient methods return Result[T, PipelineError] for
  functional composition by callers.

Provenance:

  Ported from williamdemeo.github.io (the newest lineage, 2026-07-31),
  which extracted it from agda-algebras' gh_project_populate.py during
  agda-algebras#289; originally adapted from
  formalverification/agda-native-air.  This repository is now the
  upstream; downstream projects re-vendor from here.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from _utils import Result, PipelineError, ErrorType
from _utils.command_runner import run_command


# ── Issue identifier conventions ────────────────────────────────────────────

# Issue titles are prefixed with `[MN-k]` (or `[MN-ka]` for sub-tickets
# of a fan-out parent) for cross-script identification and within-milestone
# ordering.  Examples: `[M0-1] ...`, `[M1-12] ...`, `[M2-7a] ...`.
ISSUE_ID_PATTERN = re.compile(r"^\[(M(\d+)-(\d+)([a-z]?))\]\s+(.+)$")

# Labels of the form `milestone-N-*` map issues to milestone N.  Populate
# emits these labels; update uses them to infer milestone membership when
# pulling state back from GitHub.
MILESTONE_LABEL_PATTERN = re.compile(r"^milestone-(\d+)-")


def parse_issue_id(title: str) -> Optional[tuple[str, int, int, str, str]]:
    """Parse a `[MN-k] Title` prefix into (id, milestone, ordinal, suffix, rest).

    Returns None if the title does not begin with a recognized prefix.
    Used by populate's idempotency guard and by update's title-based
    ordering.  The suffix is "" for plain `[MN-k]` ids and a single
    lowercase letter for fan-out sub-tickets like `[M2-7a]`, `[M2-7b]`.
    """
    m = ISSUE_ID_PATTERN.match(title)
    if not m:
        return None
    issue_id = m.group(1)
    ms = int(m.group(2))
    ord_ = int(m.group(3))
    suffix = m.group(4) or ""
    rest = m.group(5)
    return issue_id, ms, ord_, suffix, rest


def milestone_index_from_labels(labels: list[str]) -> Optional[int]:
    """Infer milestone number from a `milestone-N-*` label, or None if absent."""
    for label in labels:
        m = MILESTONE_LABEL_PATTERN.match(label)
        if m:
            return int(m.group(1))
    return None


def issue_id_gte(a: str, b: str) -> bool:
    """Compare issue IDs by (milestone, ordinal, suffix).

    `M1-3 >= M1-2` is True; `M0-9 >= M1-1` is False; `M2-7a >= M2-7` is
    True; `M2-7b >= M2-7a` is True.  Used by populate's --start-from
    logic and by update to order issues within a milestone.
    """
    def parse(s: str) -> tuple[int, int, str]:
        m = re.match(r"M(\d+)-(\d+)([a-z]?)", s)
        return (int(m.group(1)), int(m.group(2)), m.group(3) or "") if m else (10**9, 10**9, "")
    return parse(a) >= parse(b)


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Label:
    """A GitHub label with its display color and optional description."""
    name: str
    color: str          # six hex digits, no leading `#`
    description: str = ""


@dataclass(frozen=True)
class Milestone:
    """A GitHub milestone; `gh_number` is set after creation or lookup."""
    number: int                        # plan index: the leading integer of
                                       # the GitHub title ("1. Foundation" → 1)
    title: str                         # GitHub display title, e.g. "1. Foundation"
    description: str
    gh_number: Optional[int] = None    # GitHub-assigned milestone number

    def with_gh_number(self, n: int) -> Milestone:
        """Return a copy with `gh_number` populated (immutable update)."""
        return Milestone(self.number, self.title, self.description, n)


@dataclass(frozen=True)
class Issue:
    """A GitHub issue carrying the full information needed by all scripts."""
    id: str                                   # e.g. "M0-1"; parsed from title prefix
    title: str                                # full title including `[MN-k]` prefix
    body: str
    labels: tuple[str, ...] = ()
    milestone_idx: int = 0                    # plan index, matching the
                                              # `milestone-N-*` label group; 0
                                              # means "unclassified"
    state: str = "open"                       # "open" or "closed"
    gh_number: Optional[int] = None           # GitHub-assigned issue number;
                                              # from GitHub on the read side,
                                              # from a recorded `(#N)` heading
                                              # suffix on the parse side
    assignees: tuple[str, ...] = ()           # GitHub logins; read side only


@dataclass(frozen=True)
class RepoSnapshot:
    """Live GitHub state, fetched once per run and passed to the planners.

    One snapshot per populate run replaces the per-creation re-fetch that
    made idempotency checking O(n²) in API calls (#293 item 6).
    """
    labels: tuple[Label, ...]
    milestones: tuple[Milestone, ...]
    issues: tuple[Issue, ...]


# ── Plan-file parsing ───────────────────────────────────────────────────────
#
# The plan file (docs/GITHUB_PROJECT.md) is parsed with line-anchored
# regular expressions rather than a markdown library: the format is a
# deliberate, narrow convention (see the shipped example), and the
# scripts must run on a bare Python 3.11+ with no third-party imports.

# A `**Repository**: `owner/name`` line names the target repo so that
# `--repo` need not be repeated on every invocation.  CLI wins over file.
REPOSITORY_LINE_RE = re.compile(
    r"^\*\*Repository\*\*\s*:\s*`?([\w.-]+/[\w.-]+)`?\s*$",
    re.MULTILINE,
)

ISSUE_HEADING_RE = re.compile(r"^### Issue (M\d+-\d+[a-z]?): (.+?)[ \t]*$", re.MULTILINE)

# A recorded issue number at the end of a heading: `(#123)` as written
# back by populate, or `(#123, closed)` as rendered by update.
HEADING_NUMBER_SUFFIX_RE = re.compile(r"\s*\(#(\d+)(?:,\s*[a-z]+)?\)$")

# An issue body ends at the next issue heading, a generated-region
# marker, or one of the two structural `## ` headers known to sit
# between issue groups (`## Milestone N`, `## Summary`) — whichever
# comes first.  Deliberately NOT any `## ` heading: real plans put
# `## Description` / `## Tasks` / `## Acceptance criteria` sections
# INSIDE issue bodies, and bounding on generic headings would truncate
# them.  The two named headers are what the ancestor scripts special-
# cased for the same reason.
BODY_END_RE = re.compile(
    r"^(?:<!--\s*(?:BEGIN|END) GENERATED|## Milestone \d+\b|## Summary\b)",
    re.MULTILINE,
)

# Region markers sit between issue headings in the plan file, so the
# span-based body extraction would sweep the closing marker of each
# milestone into that milestone's final issue.  Left in place it would be
# pushed to GitHub and then read back by update into the middle of a
# generated region, closing the region early on the next round-trip.
GENERATED_MARKER_LINE = re.compile(
    r"^[ \t]*<!--\s*(?:BEGIN|END) GENERATED:[^>]*-->[ \t]*$\n?",
    re.MULTILINE,
)


@dataclass(frozen=True)
class ProjectPlan:
    """Everything populate needs, parsed from one plan file."""
    repository: Optional[str]
    milestones: tuple[Milestone, ...]
    labels: tuple[Label, ...]
    issues: tuple[Issue, ...]


def parse_repository(text: str) -> Optional[str]:
    """Extract `owner/name` from the `**Repository**:` header line, if any."""
    m = REPOSITORY_LINE_RE.search(text)
    return m.group(1) if m else None


def parse_project_plan(text: str) -> ProjectPlan:
    """Parse the structured project plan markdown into a ProjectPlan."""
    return ProjectPlan(
        repository=parse_repository(text),
        milestones=tuple(_parse_milestones(text)),
        labels=tuple(_parse_labels(text)),
        issues=tuple(_parse_issues(text)),
    )


def _parse_milestones(text: str) -> list[Milestone]:
    """Extract milestones from the ## Milestones section."""
    milestones: list[Milestone] = []

    ms_match = re.search(r"^## Milestones\s*$", text, re.MULTILINE)
    if not ms_match:
        return milestones

    ms_text = text[ms_match.end():]
    # Stop at the next top-level section.
    next_section = re.search(r"^## (?!#)", ms_text, re.MULTILINE)
    if next_section:
        ms_text = ms_text[:next_section.start()]

    # Each milestone starts with `### Milestone N — Title`.
    ms_blocks = re.split(r"^### Milestone (\d+) — (.+)$", ms_text, flags=re.MULTILINE)

    # ms_blocks[0] is preamble, then groups of (number, title, body).
    i = 1
    while i + 2 < len(ms_blocks):
        num = int(ms_blocks[i])
        title = ms_blocks[i + 1].strip()
        body = ms_blocks[i + 2].strip()

        desc_match = re.search(
            r"\*\*Description[:\*]*\*?\s*\n(.*?)(?=\*\*Exit criterion|---|\Z)",
            body, re.DOTALL
        )
        desc = desc_match.group(1).strip() if desc_match else ""

        exit_match = re.search(
            r"\*\*Exit criterion[:\*]*\*?\s*(.+?)(?=\n---|\n\n###|\Z)",
            body, re.DOTALL
        )
        exit_crit = exit_match.group(1).strip() if exit_match else ""

        full_desc = desc
        if exit_crit:
            full_desc += f"\n\n**Exit criterion:** {exit_crit}"

        milestones.append(Milestone(
            number=num,
            title=f"{num}. {title}",
            description=full_desc,
        ))
        i += 3

    return milestones


def _parse_labels(text: str) -> list[Label]:
    """Collect labels from the markdown.

    Preferred: an explicit ``## Labels`` section with entries of the form

        - `label-name` (COLORHEX) — Description

    (The separator may be em-dash, en-dash, hyphen, or colon.)

    Fallback: if no ``## Labels`` section is present, collect all label
    names referenced in issues' ``**Labels:**`` lines and give them a
    neutral gray color with no description.

    If neither yields anything, return no labels at all.  (The ancestor
    scripts shipped a hard-coded default set here; a template must not
    impose labels the plan never mentioned.)
    """
    explicit = _parse_explicit_labels(text)
    if explicit:
        return explicit
    return _collect_labels_from_issues(text)


def _parse_explicit_labels(text: str) -> list[Label]:
    """Parse the ``## Labels`` section into Label records, if it exists."""
    m = re.search(r"^## Labels\s*$", text, re.MULTILINE)
    if not m:
        return []
    start = m.end()
    next_section = re.search(r"^## (?!#)", text[start:], re.MULTILINE)
    end = start + next_section.start() if next_section else len(text)
    section = text[start:end]
    pattern = re.compile(
        r"^\s*[-*]\s+`([^`]+)`\s*\(([0-9a-fA-F]{6})\)\s*(?:—|–|-|:)\s*(.+?)\s*$",
        re.MULTILINE,
    )
    return [
        Label(mm.group(1).strip(), mm.group(2).strip().lower(), mm.group(3).strip())
        for mm in pattern.finditer(section)
    ]


def _collect_labels_from_issues(text: str) -> list[Label]:
    """Collect unique label names referenced in issues' ``**Labels:**`` lines."""
    seen: dict[str, Label] = {}
    for m in re.finditer(r"\*\*Labels:\*\*\s*(.+)", text):
        for raw in m.group(1).split(","):
            name = raw.strip().strip("`").strip()
            if name and name not in seen:
                seen[name] = Label(name, "cccccc", "")
    return list(seen.values())


def _parse_issues(text: str) -> list[Issue]:
    """Extract issues from `### Issue MN-k: Title` headings anywhere in the file."""
    issues: list[Issue] = []
    matches = list(ISSUE_HEADING_RE.finditer(text))

    for idx, match in enumerate(matches):
        issue_id = match.group(1)
        heading_title = match.group(2).strip()

        ms_idx = int(re.match(r"M(\d+)", issue_id).group(1))

        # Body runs from after the heading to the next issue heading,
        # top-level section, or END GENERATED marker.
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        stop = BODY_END_RE.search(text, start, end)
        if stop:
            end = stop.start()

        raw_body = text[start:end].strip()

        # Remove trailing --- separators.
        raw_body = re.sub(r"\n---+\s*$", "", raw_body).strip()

        # Extract labels from the **Labels:** line.
        labels_match = re.search(r"\*\*Labels:\*\*\s*(.+)", raw_body)
        labels: list[str] = []
        if labels_match:
            label_str = labels_match.group(1).strip()
            labels = [lbl.strip().strip("`") for lbl in label_str.split(",")]

        # Build the issue body: drop the metadata lines.  Assignees is
        # GitHub-owned state rendered by update; if a new issue was
        # authored by copying a rendered block, that line is not content.
        body_lines = [
            line for line in raw_body.split("\n")
            if not line.strip().startswith("**Labels:**")
            and not line.strip().startswith("**Milestone:**")
            and not line.strip().startswith("**Assignees:**")
        ]
        body = "\n".join(body_lines).strip()
        body = re.sub(r"^\n+", "", body)

        # Drop any region marker swept in from the surrounding file.
        body = GENERATED_MARKER_LINE.sub("", body).strip()

        # A recorded `(#N)` / `(#N, closed)` suffix carries the GitHub
        # number written back by a previous populate run (or rendered by
        # update); it is state, not title text.
        gh_number: Optional[int] = None
        suffix = HEADING_NUMBER_SUFFIX_RE.search(heading_title)
        if suffix:
            gh_number = int(suffix.group(1))
            heading_title = heading_title[:suffix.start()].strip()

        # Titles carry the `[MN-k]` prefix on GitHub: it is the only handle
        # gh_project_update.py has for identifying planning issues, and the
        # key populate's idempotency guard compares on.  Strip any prefix
        # already written into the heading and re-apply it, rather than
        # passing an existing one through: ISSUE_ID_PATTERN wants `\s+`
        # after the bracket, so a heading reading `[M1-1]Title` would
        # survive a startswith() check and still be unparseable.
        bare = re.sub(rf"^\[{re.escape(issue_id)}\]\s*", "", heading_title).strip()
        gh_title = f"[{issue_id}] {bare}"

        issues.append(Issue(
            id=issue_id,
            title=gh_title,
            body=body,
            labels=tuple(labels),
            milestone_idx=ms_idx,
            gh_number=gh_number,
        ))

    return issues


# ── Issue-number write-back ─────────────────────────────────────────────────

def record_issue_number(text: str, issue_id: str, gh_number: int) -> tuple[str, bool]:
    """Write `(#N)` onto the `### Issue {issue_id}:` heading line.

    Returns (new_text, found).  Line-oriented and targeted, so nothing
    else in the file can be disturbed; idempotent (an existing suffix is
    replaced).  Populate persists the file after each creation using
    this, which is what makes an interrupted run rerun-safe: every
    number already handed out by GitHub is on disk before the next
    mutation is attempted.
    """
    heading_re = re.compile(
        rf"^(### Issue {re.escape(issue_id)}: .+?)(\s*\(#\d+(?:,\s*[a-z]+)?\))?[ \t]*$"
    )
    out: list[str] = []
    found = False
    for line in text.splitlines(keepends=True):
        if not found:
            m = heading_re.match(line.rstrip("\n"))
            if m:
                newline = "\n" if line.endswith("\n") else ""
                out.append(f"{m.group(1)} (#{gh_number}){newline}")
                found = True
                continue
        out.append(line)
    return "".join(out), found


# ── Generated-region parsing (used by update and lint) ─────────────────────

BEGIN_RE = re.compile(r"<!--\s*BEGIN GENERATED:\s*([\w-]+)\s*-->")
END_RE = re.compile(r"<!--\s*END GENERATED:\s*([\w-]+)\s*-->")


@dataclass(frozen=True)
class ParsedFile:
    """A file decomposed into manual prose and named generated regions.

    Invariant: len(manuals) == len(ids) + 1.  The original file is
    reconstructible (modulo regeneration of the marked regions) as

        manuals[0] + <BEGIN ids[0]>...<END ids[0]>
        + manuals[1] + <BEGIN ids[1]>...<END ids[1]>
        + ... + manuals[-1]

    The content between markers in the original file is discarded by
    parse; update rebuilds it from GitHub state.
    """
    manuals: tuple[str, ...]
    ids: tuple[str, ...]


def parse_file(content: str) -> Result[ParsedFile, PipelineError]:
    """Split file content on BEGIN/END GENERATED marker pairs."""
    manuals: list[str] = []
    ids: list[str] = []
    pos = 0
    while True:
        begin = BEGIN_RE.search(content, pos)
        # A stray END — whether before the next BEGIN or after the last
        # one — would otherwise vanish silently into a manual segment.
        stray = END_RE.search(content, pos)
        if stray is not None and (begin is None or stray.start() < begin.start()):
            return Result.err(PipelineError(
                error_type=ErrorType.PARSING_ERROR,
                message=(
                    f"<!-- END GENERATED: {stray.group(1)} --> "
                    f"has no matching BEGIN"
                ),
                context={"offset": stray.start()},
            ))
        if begin is None:
            manuals.append(content[pos:])
            return Result.ok(ParsedFile(tuple(manuals), tuple(ids)))
        marker_id = begin.group(1)
        manuals.append(content[pos:begin.start()])
        end = END_RE.search(content, begin.end())
        if end is None:
            return Result.err(PipelineError(
                error_type=ErrorType.PARSING_ERROR,
                message=f"unterminated <!-- BEGIN GENERATED: {marker_id} -->",
                context={"offset": begin.start()},
            ))
        if end.group(1) != marker_id:
            return Result.err(PipelineError(
                error_type=ErrorType.PARSING_ERROR,
                message=(
                    f"mismatched markers: BEGIN GENERATED: {marker_id} "
                    f"closed by END GENERATED: {end.group(1)}"
                ),
                context={"begin_offset": begin.start(), "end_offset": end.start()},
            ))
        inner_begin = BEGIN_RE.search(content, begin.end(), end.start())
        if inner_begin is not None:
            return Result.err(PipelineError(
                error_type=ErrorType.PARSING_ERROR,
                message=(
                    f"nested <!-- BEGIN GENERATED: {inner_begin.group(1)} --> "
                    f"inside region '{marker_id}'"
                ),
                context={"offset": inner_begin.start()},
            ))
        ids.append(marker_id)
        pos = end.end()


# ── Pure planning (the idempotency and reconciliation logic) ────────────────

def normalize_label_name(name: str) -> str:
    """Casefold and strip all whitespace, for near-collision detection.

    `era: conway`, `era:conway`, and `Era:Conway` all normalize to
    `era:conway`: creating one of them alongside another silently forks
    the label scheme (a real incident), so the planner reports the
    collision instead of creating.
    """
    return "".join(name.casefold().split())


@dataclass(frozen=True)
class LabelPlan:
    """Per-label decisions computed from plan × snapshot."""
    to_create: tuple[Label, ...]
    existing: tuple[tuple[Label, Label], ...]    # (desired, live) exact-name matches
    collisions: tuple[tuple[Label, Label], ...]  # (desired, live) near-collisions


def plan_labels(desired: tuple[Label, ...], existing: tuple[Label, ...]) -> LabelPlan:
    """Reconcile desired labels against the repo's live labels.

    Never plans an overwrite: an exact-name match is reported as existing
    even when color or description differ (the report surfaces the
    difference; resolving it is a human decision), and a near-collision
    (same name modulo case/whitespace) is reported and skipped rather
    than silently creating a parallel scheme.
    """
    by_exact = {e.name: e for e in existing}
    by_norm: dict[str, Label] = {}
    for e in existing:
        by_norm.setdefault(normalize_label_name(e.name), e)

    to_create: list[Label] = []
    exists: list[tuple[Label, Label]] = []
    collisions: list[tuple[Label, Label]] = []
    for d in desired:
        if d.name in by_exact:
            exists.append((d, by_exact[d.name]))
        elif normalize_label_name(d.name) in by_norm:
            collisions.append((d, by_norm[normalize_label_name(d.name)]))
        else:
            to_create.append(d)
    return LabelPlan(tuple(to_create), tuple(exists), tuple(collisions))


@dataclass(frozen=True)
class MilestonePlan:
    """Per-milestone decisions computed from plan × snapshot."""
    to_create: tuple[Milestone, ...]
    existing: tuple[Milestone, ...]   # gh_number populated from the snapshot


def plan_milestones(
    desired: tuple[Milestone, ...], existing: tuple[Milestone, ...]
) -> MilestonePlan:
    """Match desired milestones against live ones by exact title."""
    by_title = {e.title: e for e in existing}
    to_create: list[Milestone] = []
    exists: list[Milestone] = []
    for d in desired:
        live = by_title.get(d.title)
        if live is not None:
            exists.append(d.with_gh_number(live.gh_number or 0))
        else:
            to_create.append(d)
    return MilestonePlan(tuple(to_create), tuple(exists))


@dataclass(frozen=True)
class IssuePlan:
    """Per-issue decisions computed from plan × snapshot."""
    to_create: tuple[Issue, ...]
    existing: tuple[tuple[Issue, Issue], ...]  # (planned, live)


def plan_issues(desired: tuple[Issue, ...], existing: tuple[Issue, ...]) -> IssuePlan:
    """Match desired issues against live ones.

    The `[MN-k]` title prefix is the primary join key (robust to
    post-creation edits of the descriptive title).  A `(#N)` number
    recorded in the plan file is the secondary key: it keeps the guard
    intact even if someone strips the prefix from the title on GitHub
    (such issues arrive from the snapshot with id="", so only the
    number can match them).
    """
    by_id = {e.id: e for e in existing if e.id}
    by_number = {e.gh_number: e for e in existing if e.gh_number is not None}

    to_create: list[Issue] = []
    exists: list[tuple[Issue, Issue]] = []
    for d in desired:
        live = by_id.get(d.id)
        if live is None and d.gh_number is not None:
            live = by_number.get(d.gh_number)
        if live is not None:
            exists.append((d, live))
        else:
            to_create.append(d)
    return IssuePlan(tuple(to_create), tuple(exists))


# ── Structural lint (pure; no network) ──────────────────────────────────────

@dataclass(frozen=True)
class Problem:
    """One structural problem found in a plan file."""
    severity: str            # "error" or "warning"
    message: str
    line: Optional[int] = None


def _line_of(text: str, offset: int) -> int:
    """1-based line number of a character offset."""
    return text.count("\n", 0, offset) + 1


def lint_plan(text: str) -> tuple[Problem, ...]:
    """Validate a plan file's structure without touching the network.

    This is the drift gate this design admits without network access:
    cheap enough to run on every PR, strict enough that populate refuses
    to run against a file with errors.

    Errors (populate must not run):
      - unbalanced / mismatched / nested / stray generated-region markers
      - duplicate region ids
      - a `### Issue ...` heading that does not match
        `### Issue MN-k: Title` (such a heading is invisible to populate
        and silently erased when update rebuilds its region)
      - duplicate issue IDs
      - duplicate milestone numbers (issue→milestone attachment keys on
        the number, so a duplicate makes attachment ambiguous)
      - duplicate label names, or two plan labels that collide after
        normalization (case/whitespace)
      - an issue whose milestone number has no `### Milestone N` entry
        (when a Milestones section exists)
      - a `milestone-N` region id with no matching milestone
      - an issue referencing a label absent from the `## Labels` section
        (when that section exists — creation on GitHub would fail)
      - a bullet inside `## Labels` that does not parse as
        `` - `name` (RRGGBB) — description ``

    Warnings:
      - no `**Repository**:` header (the CLI --repo can still supply it)
    """
    problems: list[Problem] = []

    region = parse_file(text)
    ids: tuple[str, ...] = ()
    if region.is_err:
        err = region.unwrap_err()
        offset = err.context.get("offset") or err.context.get("begin_offset")
        problems.append(Problem(
            "error", err.message,
            _line_of(text, offset) if offset is not None else None,
        ))
    else:
        ids = region.unwrap().ids
        seen_ids: set[str] = set()
        for rid in ids:
            if rid in seen_ids:
                problems.append(Problem(
                    "error", f"duplicate generated-region id '{rid}'"))
            seen_ids.add(rid)

    plan = parse_project_plan(text)

    if plan.repository is None:
        problems.append(Problem(
            "warning",
            "no `**Repository**: owner/name` header; --repo must be "
            "passed on the command line",
        ))

    ms_numbers = [m.number for m in plan.milestones]
    for n in sorted({n for n in ms_numbers if ms_numbers.count(n) > 1}):
        problems.append(Problem(
            "error",
            f"milestone number {n} is defined more than once — "
            f"issue→milestone attachment keys on the number, so populate "
            f"cannot tell which milestone issues M{n}-* belong to",
        ))

    # A heading that says `### Issue` but does not parse is worse than a
    # missing one: populate never pushes it, and the next update erases
    # it when the surrounding generated region is rebuilt from GitHub.
    for m in re.finditer(r"^### Issue\b[^\n]*$", text, re.MULTILINE):
        if not ISSUE_HEADING_RE.match(m.group(0)):
            problems.append(Problem(
                "error",
                f"malformed issue heading {m.group(0)!r} — expected "
                f"`### Issue MN-k: Title`; as written it is invisible to "
                f"populate and will be erased by the next update",
                _line_of(text, m.start()),
            ))

    # Same hazard one level up: `### Milestone 1 - Core` (hyphen, not
    # em-dash) silently parses to nothing, which would otherwise also
    # disable every milestone-consistency check below.  Only NUMBERED
    # headings are scanned — prose headings like `### Milestone
    # dependencies` are legitimate (the exemplar plans use them).
    milestone_heading_re = re.compile(r"^### Milestone (\d+) — (.+)$")
    for m in re.finditer(r"^### Milestone\s+\d+\b[^\n]*$", text, re.MULTILINE):
        if not milestone_heading_re.match(m.group(0)):
            problems.append(Problem(
                "error",
                f"malformed milestone heading {m.group(0)!r} — expected "
                f"`### Milestone N — Title` (em-dash)",
                _line_of(text, m.start()),
            ))

    issue_ids = [i.id for i in plan.issues]
    for dup in sorted({i for i in issue_ids if issue_ids.count(i) > 1}):
        problems.append(Problem(
            "error", f"duplicate issue ID {dup} — populate would create "
                     f"one issue and orphan the other heading"))

    # Guard on the SECTION's presence, not on whether anything parsed
    # from it: a Milestones section whose every heading is malformed
    # must not silently disable these consistency checks.
    if re.search(r"^## Milestones\s*$", text, re.MULTILINE):
        known = set(ms_numbers)
        for issue in plan.issues:
            if issue.milestone_idx not in known:
                problems.append(Problem(
                    "error",
                    f"issue {issue.id} refers to milestone "
                    f"{issue.milestone_idx}, which has no "
                    f"`### Milestone {issue.milestone_idx}` entry",
                ))
        for rid in ids:
            m = re.match(r"^milestone-(\d+)$", rid)
            if m and int(m.group(1)) not in known:
                problems.append(Problem(
                    "error",
                    f"region '{rid}' has no matching milestone "
                    f"{int(m.group(1))} in the Milestones section",
                ))

    problems.extend(_lint_labels(text, plan))
    return tuple(problems)


def _lint_labels(text: str, plan: ProjectPlan) -> list[Problem]:
    """Label-section checks: well-formedness, duplicates, references."""
    problems: list[Problem] = []

    section = re.search(r"^## Labels\s*$", text, re.MULTILINE)
    explicit = _parse_explicit_labels(text)

    if section:
        start = section.end()
        nxt = re.search(r"^## (?!#)", text[start:], re.MULTILINE)
        end = start + nxt.start() if nxt else len(text)
        # Every bullet in the section must parse — including ones missing
        # the backticks entirely (`- documentation (0e8a16) — ...`), which
        # would otherwise silently vanish from the label set.
        entry_re = re.compile(
            r"^\s*[-*]\s+`([^`]+)`\s*\(([0-9a-fA-F]{6})\)\s*(?:—|–|-|:)\s*\S",
        )
        for m in re.finditer(r"^\s*[-*]\s+\S[^\n]*$", text[start:end], re.MULTILINE):
            if not entry_re.match(m.group(0)):
                problems.append(Problem(
                    "error",
                    f"## Labels entry {m.group(0).strip()!r} did not parse "
                    f"(expected `- `name` (RRGGBB) — description`)",
                    _line_of(text, start + m.start()),
                ))

    names = [l.name for l in explicit]
    for dup in sorted({n for n in names if names.count(n) > 1}):
        problems.append(Problem("error", f"duplicate label `{dup}`"))
    by_norm: dict[str, str] = {}
    for n in names:
        norm = normalize_label_name(n)
        if norm in by_norm and by_norm[norm] != n:
            problems.append(Problem(
                "error",
                f"labels `{by_norm[norm]}` and `{n}` collide after "
                f"case/whitespace normalization",
            ))
        by_norm.setdefault(norm, n)

    if explicit:
        known = {normalize_label_name(n) for n in names}
        for issue in plan.issues:
            if issue.gh_number is not None:
                # Already created: its labels line is state rendered
                # back by update (and may legitimately carry labels
                # added on GitHub), not creation input.
                continue
            for lbl in issue.labels:
                if normalize_label_name(lbl) not in known:
                    problems.append(Problem(
                        "error",
                        f"issue {issue.id} references label `{lbl}`, which "
                        f"is not in the ## Labels section — issue creation "
                        f"on GitHub would fail",
                    ))
    return problems


# ── GitHub client ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GitHubClient:
    """Thin Result-returning wrapper around the `gh` CLI.

    Read side: `snapshot()` pulls labels, milestones, and issues in three
    calls total; the pure planners consume it.  Write side: plain
    creations with no existence checks and no dry-run logic — whether to
    call at all is the caller's decision, made from the plan.  (Dry-run
    faithfulness is structural: the printed plan IS the execution list.)

    The `env_prefix` flag prepends `env -u GH_TOKEN -u GITHUB_TOKEN` to
    every invocation, working around a long-standing `gh` quirk where
    these environment variables silently override the keychain-stored
    auth token.  Default-on is the safer choice locally; pass
    `env_prefix=False` wherever authentication comes *through* those
    variables — GitHub Actions included: stripping them there leaves gh
    with no credentials at all.
    """
    repo: str
    env_prefix: bool = True

    # — internal command construction ——————————————————————————————————

    def _gh(self, *args: str) -> list[str]:
        prefix = ["env", "-u", "GH_TOKEN", "-u", "GITHUB_TOKEN"] if self.env_prefix else []
        return prefix + ["gh", *args]

    def _run(self, *args: str) -> Result[str, PipelineError]:
        """Run `gh ...` and return stdout as text on success."""
        return run_command(self._gh(*args), capture_output=True, text=True).map(
            lambda proc: proc.stdout
        )

    # — read side ————————————————————————————————————————————————————————

    def snapshot(self) -> Result[RepoSnapshot, PipelineError]:
        """Fetch the full live state once: labels, milestones, issues."""
        return self.list_labels().and_then(
            lambda labels: self.list_milestones().and_then(
                lambda milestones: self.list_issues().map(
                    lambda issues: RepoSnapshot(
                        tuple(labels), tuple(milestones), tuple(issues)
                    )
                )
            )
        )

    def list_labels(self) -> Result[list[Label], PipelineError]:
        """Pull every label currently defined on the repository.

        Uses the REST endpoint with --paginate: `gh label list --limit N`
        imposes a fixed ceiling, and a silently truncated snapshot would
        defeat the collision detection it feeds.
        """
        cmd = self._run(
            "api", f"repos/{self.repo}/labels",
            "--paginate",
            "-X", "GET",
            "-F", "per_page=100",
        )
        return cmd.and_then(_parse_labels_json)

    def list_milestones(self) -> Result[list[Milestone], PipelineError]:
        """Pull every milestone (open and closed) from the repository.

        The GitHub CLI lacks first-class milestone subcommands, so we go
        through the REST API.  Returned `Milestone.number` is the leading
        integer of the title (the plan index); `gh_number` carries
        GitHub's own number for cross-reference.
        """
        cmd = self._run(
            "api", f"repos/{self.repo}/milestones",
            "--paginate",
            "-X", "GET",
            "-F", "state=all",
            "-F", "per_page=100",
        )
        return cmd.and_then(_parse_milestones_json)

    def list_issues(self) -> Result[list[Issue], PipelineError]:
        """Pull every issue (open and closed) from the repository.

        Uses the REST endpoint with --paginate: `gh issue list --limit N`
        imposes a fixed ceiling, and a truncated snapshot would make
        populate re-create issues it failed to see and update omit them
        from generated regions.  Pull requests (which the REST issues
        endpoint includes) are filtered out by the parser.

        Issues whose title carries no parseable `[MN-k]` prefix are
        returned with `id=""`: populate needs them (their number is the
        fallback join key when a title was edited on GitHub), while
        update filters them out of rendering.

        Returned `Issue.milestone_idx` is inferred from the issue's
        `milestone-N-*` label rather than from GitHub's milestone
        assignment; the label is the source of truth in this convention
        because it survives milestone renames.
        """
        cmd = self._run(
            "api", f"repos/{self.repo}/issues",
            "--paginate",
            "-X", "GET",
            "-F", "state=all",
            "-F", "per_page=100",
        )
        return cmd.and_then(_parse_issues_json)

    # — write side ———————————————————————————————————————————————————————

    def create_label(self, label: Label) -> Result[None, PipelineError]:
        """Create `label` on GitHub.  Never `--force`: overwriting an
        existing label's color or description is a human decision."""
        return self._run(
            "label", "create", label.name,
            "--repo", self.repo,
            "--color", label.color,
            "--description", label.description,
        ).map(lambda _: None)

    def create_milestone(self, ms: Milestone) -> Result[Milestone, PipelineError]:
        """Create `ms` on GitHub; returns it with gh_number populated."""
        return self._run(
            "api", f"repos/{self.repo}/milestones",
            "-X", "POST",
            "-f", f"title={ms.title}",
            "-f", f"description={ms.description}",
        ).and_then(_parse_milestone_create_response).map(ms.with_gh_number)

    def create_issue(
        self, issue: Issue, milestone_title: Optional[str]
    ) -> Result[int, PipelineError]:
        """Create `issue` on GitHub; returns the new issue number."""
        args = [
            "issue", "create",
            "--repo", self.repo,
            "--title", issue.title,
            "--body", issue.body,
        ]
        for label in issue.labels:
            args += ["--label", label]
        if milestone_title is not None:
            args += ["--milestone", milestone_title]
        return self._run(*args).and_then(_parse_issue_create_response)


# ── JSON parsers ─────────────────────────────────────────────────────────────
#
# Module-level pure functions to keep GitHubClient methods short and to
# make each parser independently testable.  Each returns Result so the
# pipeline composes through `and_then` without intermediate try/except.

def _parse_labels_json(stdout: str) -> Result[list[Label], PipelineError]:
    return _parse_json(stdout, "labels").map(
        lambda data: [
            Label(
                name=item["name"],
                color=item.get("color", "cccccc"),
                description=item.get("description", "") or "",
            )
            for item in data
        ]
    )


def _parse_milestones_json(stdout: str) -> Result[list[Milestone], PipelineError]:
    """Convert the GitHub REST `repos/.../milestones` response into Milestones.

    The tooling titles each milestone `N. Title`; the leading integer is
    the plan index.  Milestones whose title does not match this
    convention are skipped (with no error): they were not created by this
    tooling and do not belong to the plan.
    """
    title_pattern = re.compile(r"^(\d+)\.\s+(.+)$")

    def to_milestones(data: list[dict]) -> list[Milestone]:
        out: list[Milestone] = []
        for item in data:
            m = title_pattern.match(item.get("title", ""))
            if not m:
                continue
            out.append(Milestone(
                number=int(m.group(1)),
                title=item["title"],
                description=item.get("description") or "",
                gh_number=item["number"],
            ))
        return sorted(out, key=lambda x: x.number)
    return _parse_json(stdout, "milestones").map(to_milestones)


def _parse_issues_json(stdout: str) -> Result[list[Issue], PipelineError]:
    def to_issues(data: list[dict]) -> list[Issue]:
        out: list[Issue] = []
        for item in data:
            if "pull_request" in item:
                # The REST issues endpoint returns pull requests too;
                # they are never planning issues.
                continue
            title = item.get("title", "")
            parsed = parse_issue_id(title)
            # An unparseable title (e.g. an ad-hoc bug report, or a
            # planning issue whose prefix someone stripped on GitHub) is
            # kept with id="" rather than dropped: populate matches such
            # issues by their recorded (#N) number — dropping them here
            # would make that fallback unreachable and re-create the
            # issue.  Update filters id="" records out of rendering.
            issue_id, ms_idx = ("", None)
            if parsed is not None:
                issue_id, ms_idx, _ord, _suffix, _rest = parsed
            label_names = tuple(lbl["name"] for lbl in item.get("labels", []) or [])
            # Prefer the milestone-N-* label; fall back to the leading
            # integer of GitHub's milestone title.  The label is canonical
            # because it survives milestone-title edits.
            inferred = milestone_index_from_labels(list(label_names))
            if inferred is None:
                gh_ms = item.get("milestone") or {}
                gh_title = gh_ms.get("title", "")
                tm = re.match(r"^(\d+)\.", gh_title)
                inferred = int(tm.group(1)) if tm else (ms_idx if ms_idx is not None else 0)
            out.append(Issue(
                id=issue_id,
                title=title,
                body=item.get("body") or "",
                labels=label_names,
                milestone_idx=inferred,
                state=item.get("state", "open").lower(),
                gh_number=item.get("number"),
                assignees=tuple(
                    a["login"] for a in item.get("assignees", []) or []
                ),
            ))
        return out
    return _parse_json(stdout, "issues").map(to_issues)


def _parse_json(stdout: str, kind: str) -> Result[list[dict], PipelineError]:
    """Decode the JSON body of a `gh` invocation, with structured errors.

    `gh api --paginate` concatenates each page as a separate JSON
    document (`[...][...]`), so this decodes document-by-document with
    raw_decode and concatenates the lists — a single json.loads would
    fail with "Extra data" on the 101st item.  (gh's own --slurp flag
    would do this server-side, but it requires gh >= 2.47; decoding here
    keeps the documented floor at "any gh".)
    """
    decoder = json.JSONDecoder()
    items: list[dict] = []
    pos = 0
    end = len(stdout)
    while True:
        while pos < end and stdout[pos].isspace():
            pos += 1
        if pos >= end:
            break
        try:
            data, pos = decoder.raw_decode(stdout, pos)
        except json.JSONDecodeError as e:
            return Result.err(PipelineError(
                error_type=ErrorType.PARSING_ERROR,
                message=f"failed to decode {kind} JSON from `gh`",
                cause=e,
                context={"stdout_preview": stdout[:500]},
            ))
        if not isinstance(data, list):
            return Result.err(PipelineError(
                error_type=ErrorType.PARSING_ERROR,
                message=f"expected a JSON list of {kind}, got {type(data).__name__}",
            ))
        items.extend(data)
    return Result.ok(items)


def _parse_milestone_create_response(stdout: str) -> Result[int, PipelineError]:
    try:
        data = json.loads(stdout)
        return Result.ok(int(data["number"]))
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        return Result.err(PipelineError(
            error_type=ErrorType.PARSING_ERROR,
            message="could not extract milestone number from `gh api` response",
            cause=e,
        ))


def _parse_issue_create_response(stdout: str) -> Result[int, PipelineError]:
    """`gh issue create` prints the issue URL; extract the trailing number."""
    m = re.search(r"/issues/(\d+)\s*$", stdout.strip())
    if not m:
        return Result.err(PipelineError(
            error_type=ErrorType.PARSING_ERROR,
            message="could not extract issue number from `gh issue create` output",
            context={"stdout": stdout},
        ))
    return Result.ok(int(m.group(1)))
