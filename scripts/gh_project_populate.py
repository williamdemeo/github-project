#!/usr/bin/env python3
"""
File: scripts/gh_project_populate.py

Description:
  Create GitHub labels, milestones, and issues from a structured Markdown
  project plan (docs/GITHUB_PROJECT.md), and write the new issue numbers
  back into the plan file.

  This is the file → GitHub direction; the inverse (GitHub → file) is
  gh_project_update.py.

Usage:
    python3 scripts/gh_project_populate.py docs/GITHUB_PROJECT.md [OPTIONS]

  The target repository comes from the plan file's `**Repository**:`
  header, or from --repo (which wins when both are present).

Behavior guarantees:
  - Idempotent: one snapshot of live GitHub state is fetched per run and
    everything that already exists is skipped — labels by exact name,
    milestones by exact title, issues by their `[MN-k]` title prefix (or
    by a `(#N)` number previously recorded in the plan file).
  - Crash-safe: each created issue's number is written back into the
    plan file (as a `(#N)` heading suffix) and persisted BEFORE the next
    remote mutation, so an interrupted run never causes duplicates on
    rerun.
  - Label reconciliation, not imposition: existing labels are never
    overwritten (color/description differences are reported), and
    near-collisions (`era: conway` vs an existing `era:conway`) are
    reported and skipped rather than silently creating a parallel
    scheme.
  - Dry-run-first: --dry-run prints exactly the plan a real run would
    execute (reads still happen; writes never do).

Options:
  --repo OWNER/NAME  Target repository (overrides the file header)
  --dry-run          Print the plan without mutating anything
  --milestones-only  Only create milestones
  --labels-only      Only create labels
  --issues-only      Only create issues (their milestones and every
                     referenced label must already exist)
  --skip-labels      Skip label creation
  --start-from ID    Create only issues with ID >= this (e.g. M1-3)
  --sync-bodies      Create nothing; push existing issues' bodies and
                     milestone descriptions from the file to GitHub —
                     the per-issue inverse of update.  Content
                     divergence is refused per item unless --force
                     (last writer wins); wrapping-only differences push
                     safely without it
  --force            With --sync-bodies: push divergent content anyway
  --keep-line-breaks Push bodies exactly as authored; by default hard
                     line breaks in prose are stripped so GitHub
                     soft-wraps it
  --delay SECONDS    Pause between mutating calls (default: 1.0)
  --yes              Skip the interactive confirmation
  --env-prefix       Prefix gh with `env -u GH_TOKEN -u GITHUB_TOKEN`
                     (default; works around token-precedence quirks)
  --no-env-prefix    Disable the env prefix.  Needed wherever
                     authentication comes *through* those variables,
                     GitHub Actions included.

Exit codes:
  0  everything requested was created or already existed
  1  some mutations failed or were skipped (collisions, missing labels)
  2  the run could not proceed (unreadable file, lint errors, no repo,
     snapshot failure)
"""
from __future__ import annotations

import argparse
import re
import sys
import textwrap
import time
from dataclasses import dataclass
from typing import Callable
from pathlib import Path

# Permit `python3 scripts/gh_project_populate.py ...` from the repo root
# by ensuring this file's directory is on sys.path before importing the
# sibling private modules.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _gh_project_lib import (  # noqa: E402
    EMPTY_BODY_PLACEHOLDER,
    MARKER_IN_BODY_RE,
    neutralize_markers,
    GitHubClient,
    IssuePlan,
    LabelPlan,
    MilestonePlan,
    ProjectPlan,
    RepoSnapshot,
    issue_id_gte,
    lint_plan,
    parse_project_plan,
    plan_issues,
    plan_labels,
    plan_milestones,
    record_issue_number,
)
from _utils.file_ops import read_text, write_text  # noqa: E402
from _utils.text_unwrap import unwrap  # noqa: E402


# ── Plan reporting (shared verbatim by dry-run and live runs) ───────────────

def print_label_plan(plan: LabelPlan) -> None:
    for label in plan.to_create:
        print(f"  + create label: {label.name} (#{label.color})")
    for desired, live in plan.existing:
        print(f"  - exists: label {live.name}")
        if (desired.color.lower() != live.color.lower()
                or desired.description != live.description):
            print(
                f"      note: differs from the plan "
                f"(plan #{desired.color} {desired.description!r}, "
                f"live #{live.color} {live.description!r}) — not overwritten"
            )
    for desired, live in plan.collisions:
        print(
            f"  ! collision: plan label `{desired.name}` vs existing "
            f"`{live.name}` (same name modulo case/whitespace) — skipped; "
            f"rename one side, then re-run"
        )


def print_milestone_plan(plan: MilestonePlan) -> None:
    for ms in plan.to_create:
        print(f"  + create milestone: {ms.title}")
    for ms in plan.existing:
        print(f"  - exists: milestone #{ms.gh_number} {ms.title}")


def issue_blocker(
    issue,
    available_milestones: set[int],
    declared_milestones: set[int],
    available_labels: set[str],
) -> str | None:
    """Why this issue cannot be created, or None if it can.

    Shared by the dry-run report and the live execution loop, so the
    dry run prints — and exits with — exactly what a real run would do.
    """
    missing = [
        lbl for lbl in issue.labels if lbl.casefold() not in available_labels
    ]
    if missing:
        return (
            f"references unavailable label(s) "
            f"{', '.join(f'`{m}`' for m in missing)} "
            f"(not on GitHub and not created this run)"
        )
    if (issue.milestone_idx not in available_milestones
            and issue.milestone_idx in declared_milestones):
        return (
            f"milestone {issue.milestone_idx} is declared in the plan but "
            f"not available on GitHub (create milestones first, then re-run)"
        )
    return None


def _normalized(body: str) -> str:
    """Comparison form: GitHub stores web edits with CRLF, and TRAILING
    whitespace is never meaningful.  Leading whitespace stays — a
    four-space-indented first line is a code block, and eating it would
    make genuinely different bodies compare equal (and in-sync
    short-circuits even --force)."""
    return body.replace("\r\n", "\n").rstrip()


# Markdown hard breaks: two+ trailing spaces, or a backslash, before a
# newline.  unwrap() deliberately removes them, so they are invisible
# to the reflow equivalence — but on the GitHub side they are intended
# rendering (<br>) that a push would destroy.
_HARD_BREAK_RE = re.compile(r"(?:[ ]{2,}|\\)\n")


def classify_sync(
    file_body: str, github_body: str, *, allow_empty_placeholder: bool = False
) -> str:
    """How the plan file's body relates to GitHub's, with no stored base.

    With allow_empty_placeholder (issue pairs only), a file body that is
    exactly update's empty-body sentinel counts as in sync with an empty
    GitHub body: that text is update's own rendering of "no description",
    and pushing it would write the placeholder into a deliberately
    empty issue.  Milestones don't get this rule — a milestone
    description could legitimately BE that text.

    - "in-sync":   identical after CRLF/trailing-whitespace
                   normalization (leading whitespace is meaningful and
                   preserved).
    - "reflow":    identical after unwrap() of both sides — the content
                   matches and only line wrapping differs, so pushing
                   loses neither words nor GitHub-side formatting.  This
                   is the case --sync-bodies exists for.
    - "divergent": the content differs, or GitHub's body carries
                   explicit hard-break syntax (trailing double-space or
                   backslash before a newline) that reflow would
                   silently destroy.  The engine stores no base version,
                   so it cannot tell which side moved — refused by
                   default; --force pushes the file's version (last
                   writer wins).  Hard breaks on the FILE side alone do
                   not diverge: the push carries them to GitHub intact.
    """
    ours, theirs = _normalized(file_body), _normalized(github_body)
    if ours == theirs:
        return "in-sync"
    if allow_empty_placeholder and not theirs \
            and ours == EMPTY_BODY_PLACEHOLDER:
        return "in-sync"
    # update writes the DEFANGED form of marker-bearing GitHub bodies
    # into the file (neutralize_markers), so a file that is exactly the
    # escaped mirror of GitHub is in sync — pushing it would replace the
    # real body with our escape artifacts.
    if ours == neutralize_markers(theirs):
        return "in-sync"
    if _HARD_BREAK_RE.search(theirs):
        return "divergent"
    # Raw region-marker text on the GitHub side gets no reflow shortcut
    # either: the file can only ever hold the escaped form, so any push
    # would send escape artifacts upstream.  --force remains true
    # last-writer-wins, escapes included.
    if MARKER_IN_BODY_RE.search(theirs):
        return "divergent"
    # File-side hard breaks are defanged for the EQUIVALENCE test only:
    # the two-space form vanishes under unwrap's rstrip anyway, but the
    # backslash form would survive the join ("a\\ b") and spuriously
    # demand --force for a push that preserves the break verbatim.
    if unwrap(_HARD_BREAK_RE.sub("\n", ours)) == unwrap(theirs):
        return "reflow"
    return "divergent"


@dataclass(frozen=True)
class SyncTarget:
    """One existing GitHub item --sync-bodies may rewrite."""
    label: str
    kind: str                                  # "issue" | "milestone"
    file_text: str
    snapshot_text: str
    fetch: Callable[[], object]                # () -> Result[str, PipelineError]
    push: Callable[[], object]                 # () -> Result[None, PipelineError]


def execute_sync_bodies(
    targets: list[SyncTarget],
    force: bool,
    dry_run: bool,
    delay: float,
) -> int:
    """Push plan-file bodies/descriptions to their existing GitHub
    counterparts.  Returns the number of failures + refusals.

    Dry runs classify against the run's snapshot; LIVE runs re-fetch
    each target immediately before mutating and classify against that
    fresh text, so the refuse-on-divergence guarantee also covers edits
    made after the snapshot — e.g. while the confirmation prompt sat
    open.  (GitHub's API has no conditional issue update, so a
    milliseconds-wide window remains; the revalidation shrinks it from
    prompt-sized to that.)  Refusals are per-item — one divergent body
    does not block the reflow-safe ones.
    """
    in_sync = pushed = refused = failed = 0
    for target in targets:
        if dry_run:
            github_text = target.snapshot_text
        else:
            fresh = target.fetch()
            if fresh.is_err:
                failed += 1
                print(f"  ! {target.label}: revalidation failed: "
                      f"{fresh.unwrap_err()}")
                continue
            github_text = fresh.unwrap()
        verdict = classify_sync(
            target.file_text, github_text,
            allow_empty_placeholder=target.kind == "issue",
        )
        if verdict == "in-sync":
            in_sync += 1
            print(f"  = in sync: {target.label}")
            continue
        if verdict == "divergent" and not force:
            refused += 1
            print(
                f"  ! divergent: {target.label} — content differs beyond "
                f"line wrapping and no base is stored to tell which side "
                f"moved; re-run with --force to push the file's version"
            )
            continue
        tag = "forced push" if verdict == "divergent" else "reflow"
        if dry_run:
            pushed += 1
            print(f"  ~ would push ({tag}): {target.label}")
            continue
        result = target.push()
        if result.is_ok:
            pushed += 1
            print(f"  ~ pushed ({tag}): {target.label}")
        else:
            failed += 1
            print(f"  ! {target.label}: {result.unwrap_err()}")
        time.sleep(delay)
    verb = "would push" if dry_run else "pushed"
    print()
    print(f"  sync: {pushed} {verb}, {in_sync} already in sync, "
          f"{refused} divergent (refused), {failed} failed")
    return refused + failed


def print_issue_plan(
    plan: IssuePlan,
    available_milestones: set[int],
    declared_milestones: set[int],
    available_labels: set[str],
) -> int:
    """Print the issue plan; returns the number of blocked issues."""
    blocked = 0
    for issue in plan.to_create:
        reason = issue_blocker(
            issue, available_milestones, declared_milestones, available_labels
        )
        if reason is None:
            print(f"  + create issue: {issue.title}")
        else:
            blocked += 1
            print(f"  ! blocked: issue {issue.id} — {reason}")
    for _, live in plan.existing:
        print(f"  - exists: issue #{live.gh_number} {live.title}")
    return blocked


def _stage_header(title: str) -> None:
    print("═" * 60)
    print(title)
    print("═" * 60)


# ── Execution (the only functions that mutate GitHub or the file) ───────────

def execute_labels(
    client: GitHubClient, plan: LabelPlan, delay: float
) -> int:
    """Create the planned labels; returns the number of failures."""
    failed = 0
    for label in plan.to_create:
        result = client.create_label(label)
        if result.is_ok:
            print(f"  created label: {label.name}")
        else:
            failed += 1
            print(f"  ! label {label.name}: {result.unwrap_err()}")
        time.sleep(delay)
    return failed


def execute_milestones(
    client: GitHubClient, plan: MilestonePlan, delay: float
) -> tuple[dict[int, str], int]:
    """Create the planned milestones; returns ({plan index → title}, failures)."""
    title_map = {ms.number: ms.title for ms in plan.existing}
    failed = 0
    for ms in plan.to_create:
        result = client.create_milestone(ms)
        if result.is_ok:
            created = result.unwrap()
            title_map[created.number] = created.title
            print(f"  created milestone #{created.gh_number}: {created.title}")
        else:
            failed += 1
            print(f"  ! milestone {ms.title}: {result.unwrap_err()}")
        time.sleep(delay)
    return title_map, failed


def execute_issues(
    client: GitHubClient,
    plan: IssuePlan,
    ms_title_map: dict[int, str],
    declared_milestones: set[int],
    available_labels: set[str],
    md_path: Path,
    text: str,
    delay: float,
) -> tuple[str, int, int]:
    """Create the planned issues with immediate number write-back.

    Returns (current file text, created, failures).  The counts are
    explicit so the caller's summary stays honest when the loop aborts
    early — `planned - failed` is NOT the number created in that case.

    The plan file is persisted after EACH creation, before the next
    remote mutation — that is the crash-safety contract, so a write-back
    that cannot be persisted ABORTS the run (counted as a failure)
    rather than continuing to mutate GitHub while numbers silently stop
    landing on disk.  Nothing is lost by aborting: created issues are
    matched by the snapshot on the next run.

    An issue whose plan-declared milestone is unavailable (creation
    failed, or --issues-only before milestones exist) is skipped and
    counted as a failure: creating it milestone-less would leave GitHub
    state permanently incomplete, since populate never revisits existing
    issues.  Issues referencing a milestone the plan never declared are
    created without one (a plan with no Milestones section is legal).
    """
    created = 0
    failed = 0
    for issue in plan.to_create:
        reason = issue_blocker(
            issue, set(ms_title_map), declared_milestones, available_labels
        )
        if reason is not None:
            failed += 1
            print(f"  ! issue {issue.id}: skipped — {reason}")
            continue

        ms_title = ms_title_map.get(issue.milestone_idx)
        if ms_title is None and issue.milestone_idx != 0:
            print(
                f"  ~ issue {issue.id}: milestone {issue.milestone_idx} "
                f"is not declared in the plan; creating without a "
                f"milestone"
            )

        result = client.create_issue(issue, milestone_title=ms_title)
        if result.is_err:
            failed += 1
            print(f"  ! issue {issue.id}: {result.unwrap_err()}")
            time.sleep(delay)
            continue

        gh_number = result.unwrap()
        created += 1
        print(f"  created issue #{gh_number}: {issue.title}")

        new_text, found = record_issue_number(text, issue.id, gh_number)
        write_result = write_text(md_path, new_text) if found else None
        if not found or write_result.is_err:
            reason = (
                f"heading for {issue.id} not found"
                if not found else str(write_result.unwrap_err())
            )
            remaining = len(plan.to_create) - created - failed
            failed += 1
            print(
                f"  ! could not persist #{gh_number} for {issue.id} "
                f"({reason}); aborting before further mutations "
                f"({remaining} issue(s) not attempted).  Issue "
                f"#{gh_number} IS live on GitHub; fix the problem and "
                f"re-run — the snapshot match makes the rerun safe.",
                file=sys.stderr,
            )
            return text, created, failed
        text = new_text
        time.sleep(delay)
    return text, created, failed


def run_sync_bodies(
    client: GitHubClient,
    snapshot: RepoSnapshot,
    milestone_plan: MilestonePlan,
    issue_plan: IssuePlan,
    args: argparse.Namespace,
) -> int:
    """The --sync-bodies mode: push file bodies to EXISTING GitHub
    counterparts; never creates anything (issue #7)."""
    issue_targets = [
        SyncTarget(
            label=f"issue {desired.id} (#{live.gh_number})",
            kind="issue",
            file_text=desired.body,
            snapshot_text=live.body,
            fetch=lambda n=live.gh_number: client.get_issue_body(n),
            push=lambda n=live.gh_number, b=desired.body:
                client.update_issue_body(n, b),
        )
        for desired, live in issue_plan.existing
        if live.gh_number is not None
    ]
    live_desc = {m.gh_number: m.description for m in snapshot.milestones}
    milestone_targets = [
        SyncTarget(
            label=f"milestone {ms.title} (#{ms.gh_number})",
            kind="milestone",
            file_text=ms.description,
            snapshot_text=live_desc.get(ms.gh_number, ""),
            fetch=lambda n=ms.gh_number: client.get_milestone_description(n),
            push=lambda n=ms.gh_number, d=ms.description:
                client.update_milestone_description(n, d),
        )
        for ms in milestone_plan.existing
        if ms.gh_number
    ]

    _stage_header(
        "SYNCING BODIES (file → GitHub, existing items only)"
        + ("  [dry run]" if args.dry_run else "")
    )
    targets = issue_targets + milestone_targets
    if not targets:
        print("  nothing to sync: no plan issue or milestone exists on "
              "GitHub yet")
    if not args.dry_run and targets and not args.yes:
        print(f"This may rewrite up to {len(issue_targets)} issue bodies "
              f"and {len(milestone_targets)} milestone descriptions on "
              f"GitHub.")
        response = input("Continue? [y/N] ").strip().lower()
        if response not in ("y", "yes"):
            print("Aborted.")
            return 0

    problems = execute_sync_bodies(
        targets, force=args.force, dry_run=args.dry_run, delay=args.delay,
    )
    if issue_plan.to_create:
        print(
            f"  note: {len(issue_plan.to_create)} plan issue(s) do not "
            f"exist on GitHub — --sync-bodies never creates; run populate "
            f"without it first"
        )
    return 1 if problems else 0


# ── Main ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Populate a GitHub repository from a structured Markdown plan.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Dry run — see what would be created:
              python3 scripts/gh_project_populate.py docs/GITHUB_PROJECT.md --dry-run

              # Create everything (prompts for confirmation):
              python3 scripts/gh_project_populate.py docs/GITHUB_PROJECT.md

              # Resume from a specific issue:
              python3 scripts/gh_project_populate.py docs/GITHUB_PROJECT.md \\
                  --issues-only --start-from M1-3
        """),
    )
    parser.add_argument("markdown", type=Path, help="Path to the project plan markdown file")
    parser.add_argument("--repo", help="GitHub repo (owner/name); overrides the file header")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without executing")
    # Mutually exclusive: any pair of these would deselect every stage
    # and silently do nothing.
    stage = parser.add_mutually_exclusive_group()
    stage.add_argument("--milestones-only", action="store_true")
    stage.add_argument("--labels-only", action="store_true")
    stage.add_argument("--issues-only", action="store_true")
    stage.add_argument("--sync-bodies", action="store_true",
                       help="create nothing; push existing issues' bodies "
                            "(and milestone descriptions) from the plan file "
                            "to GitHub — the per-issue inverse of update.  "
                            "Refuses on content divergence unless --force")
    parser.add_argument("--force", action="store_true",
                        help="with --sync-bodies: push even when the GitHub "
                             "side has content changes (last writer wins)")
    parser.add_argument("--skip-labels", action="store_true")
    parser.add_argument("--keep-line-breaks", action="store_true",
                        help="push bodies exactly as authored; by default "
                             "hard line breaks in prose are stripped so "
                             "GitHub soft-wraps it")
    parser.add_argument("--start-from", type=str, default=None,
                        help="Create only issues with ID >= this (e.g. M1-3)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Seconds between mutating API calls (default: 1.0)")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the interactive confirmation")
    parser.add_argument("--env-prefix", action="store_true", default=True,
                        help="Prefix gh with `env -u GH_TOKEN -u GITHUB_TOKEN` (default)")
    parser.add_argument("--no-env-prefix", action="store_true",
                        help="Don't prefix gh commands with env")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.no_env_prefix:
        args.env_prefix = False
    if args.force and not args.sync_bodies:
        print("error: --force only applies to --sync-bodies", file=sys.stderr)
        return 2

    do_labels = not args.issues_only and not args.milestones_only and not args.skip_labels
    do_milestones = not args.issues_only and not args.labels_only
    do_issues = not args.milestones_only and not args.labels_only

    # ── Read and validate ────────────────────────────────────────────────
    read = read_text(args.markdown)
    if read.is_err:
        print(f"error: {read.unwrap_err()}", file=sys.stderr)
        return 2
    text = read.unwrap()

    problems = lint_plan(text)
    for p in problems:
        loc = f":{p.line}" if p.line else ""
        print(f"{args.markdown}{loc}: {p.severity}: {p.message}", file=sys.stderr)
    if any(p.severity == "error" for p in problems):
        print("error: fix the plan file before populating", file=sys.stderr)
        return 2

    # By default, prose is unwrapped before parsing so pushed issue
    # bodies and milestone descriptions reach GitHub without authored
    # hard line breaks (GitHub soft-wraps).  The transform is
    # structure-preserving — the plan parses identically, only bodies
    # and descriptions reflow (pinned by test_text_unwrap) — and the
    # file on disk is never rewritten by it: the (#N) write-back still
    # edits the authored text, and the post-populate `update` run is
    # what lands normalized content in the file.
    if args.keep_line_breaks:
        effective = text
        print("Line breaks: preserved as authored (--keep-line-breaks)")
    else:
        effective = unwrap(text)
        print("Line breaks: stripped from prose before pushing "
              "(use --keep-line-breaks to preserve)")

    plan: ProjectPlan = parse_project_plan(effective)
    repo = args.repo or plan.repository
    if repo is None:
        print(
            "error: no repository given — add a `**Repository**: owner/name` "
            "header to the plan file or pass --repo",
            file=sys.stderr,
        )
        return 2

    desired_issues = plan.issues
    if args.start_from:
        before = len(desired_issues)
        desired_issues = tuple(
            i for i in desired_issues if issue_id_gte(i.id, args.start_from)
        )
        print(f"--start-from {args.start_from}: "
              f"skipping {before - len(desired_issues)} earlier issue(s)")

    print(f"Parsed {args.markdown}: {len(plan.milestones)} milestones, "
          f"{len(plan.labels)} labels, {len(plan.issues)} issues")
    print(f"Target repo: {repo}{'  (dry-run)' if args.dry_run else ''}")
    print()

    # ── Snapshot (one fetch per run — reads happen even under --dry-run,
    #    so the printed plan is exactly what a live run would execute) ────
    client = GitHubClient(repo=repo, env_prefix=args.env_prefix)
    snap_result = client.snapshot()
    if snap_result.is_err:
        print(f"error: could not fetch repository state: "
              f"{snap_result.unwrap_err()}", file=sys.stderr)
        return 2
    snapshot: RepoSnapshot = snap_result.unwrap()

    # ── Plan ─────────────────────────────────────────────────────────────
    label_plan = plan_labels(plan.labels, snapshot.labels)
    milestone_plan = plan_milestones(plan.milestones, snapshot.milestones)
    issue_plan = plan_issues(desired_issues, snapshot.issues)

    if args.sync_bodies:
        return run_sync_bodies(
            client, snapshot, milestone_plan, issue_plan, args
        )

    # Availability as it WILL stand when the issues stage runs, given
    # the selected stages: entities already on GitHub, plus those this
    # run will create first.  Feeding these to the shared issue_blocker
    # makes the printed plan — and the dry-run exit code — match what
    # execution would actually do (e.g. `--dry-run --issues-only` on a
    # repo with no milestones reports the blocks a real run would hit).
    declared_milestones = {m.number for m in plan.milestones}
    prospective_milestones = {m.number for m in milestone_plan.existing}
    if do_milestones:
        prospective_milestones |= {m.number for m in milestone_plan.to_create}
    prospective_labels = {lbl.name.casefold() for lbl in snapshot.labels}
    if do_labels:
        prospective_labels |= {lbl.name.casefold() for lbl in label_plan.to_create}

    blocked = 0
    if do_labels:
        _stage_header("LABELS")
        print_label_plan(label_plan)
        print()
    if do_milestones:
        _stage_header("MILESTONES")
        print_milestone_plan(milestone_plan)
        print()
    if do_issues:
        _stage_header("ISSUES")
        blocked = print_issue_plan(
            issue_plan, prospective_milestones, declared_milestones,
            prospective_labels,
        )
        print()

    collisions = len(label_plan.collisions) if do_labels else 0

    if args.dry_run:
        print("Dry run — nothing was created.")
        return 1 if collisions or blocked else 0

    # ── Confirm ──────────────────────────────────────────────────────────
    to_do = []
    if do_labels:
        to_do.append(f"{len(label_plan.to_create)} labels")
    if do_milestones:
        to_do.append(f"{len(milestone_plan.to_create)} milestones")
    if do_issues:
        to_do.append(f"{len(issue_plan.to_create)} issues")
    print(f"This will create: {', '.join(to_do)}")
    if not args.yes:
        response = input("Continue? [y/N] ").strip().lower()
        if response not in ("y", "yes"):
            print("Aborted.")
            return 0
    print()

    # ── Execute ──────────────────────────────────────────────────────────
    failures = 0

    if do_labels:
        _stage_header("CREATING LABELS")
        failures += execute_labels(client, label_plan, args.delay)
        print(f"  labels: {len(label_plan.to_create)} planned, "
              f"{len(label_plan.existing)} already existed, "
              f"{collisions} collisions skipped")
        print()

    ms_title_map: dict[int, str] = {ms.number: ms.title
                                    for ms in milestone_plan.existing}
    if do_milestones:
        _stage_header("CREATING MILESTONES")
        ms_title_map, ms_failed = execute_milestones(
            client, milestone_plan, args.delay
        )
        failures += ms_failed
        print(f"  milestones: {len(milestone_plan.to_create)} planned, "
              f"{len(milestone_plan.existing)} already existed")
        print()

    if do_issues:
        _stage_header("CREATING ISSUES")
        # Labels planned for creation are available only if the labels
        # stage actually ran this invocation.
        available = {lbl.name.casefold() for lbl in snapshot.labels}
        if do_labels:
            available |= {lbl.name.casefold() for lbl in label_plan.to_create}
        _text, issues_created, issue_failed = execute_issues(
            client, issue_plan, ms_title_map,
            declared_milestones, available,
            args.markdown, text, args.delay,
        )
        failures += issue_failed
        not_attempted = len(issue_plan.to_create) - issues_created - issue_failed
        print()
        _stage_header(
            f"DONE: {issues_created} issues created, "
            f"{len(issue_plan.existing)} already existed, "
            f"{issue_failed} failed or skipped, "
            f"{not_attempted} not attempted"
        )
        if issues_created and not issue_failed:
            # Only after a FULLY successful run: after a partial
            # failure this advice would be wrong twice over — the file
            # does not yet record every number, and normalizing via
            # update from incomplete GitHub state would drop the
            # not-yet-created issue definitions from the plan.  Failed
            # runs keep the per-issue rerun guidance printed above.
            #
            # A fresh plan's first `make update-check` is ALWAYS stale:
            # update's canonical rendering drops the **Milestone:**
            # lines, reorders labels to GitHub's order, and trims the
            # trailing --- separators.  Point at the normalization run
            # so that first failure is not read as a bug.
            print()
            print("Next steps:")
            print("  1. commit this file (it now records the issue numbers)")
            print("  2. run a plain `make update` once and commit the result —")
            print("     it normalizes the generated regions to the engine's")
            print("     canonical rendering (the first `make update-check` on")
            print("     a fresh plan is ALWAYS stale otherwise)")
            print("  3. from then on, `make update-check` is the drift gate")

    if collisions:
        print(f"note: {collisions} label collision(s) were skipped — "
              f"see the LABELS report above", file=sys.stderr)
    return 1 if failures or collisions else 0


if __name__ == "__main__":
    sys.exit(main())
