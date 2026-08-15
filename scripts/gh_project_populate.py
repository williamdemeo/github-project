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
  --issues-only      Only create issues (milestones must already exist)
  --skip-labels      Skip label creation
  --start-from ID    Create only issues with ID >= this (e.g. M1-3)
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
import sys
import textwrap
import time
from pathlib import Path

# Permit `python3 scripts/gh_project_populate.py ...` from the repo root
# by ensuring this file's directory is on sys.path before importing the
# sibling private modules.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _gh_project_lib import (  # noqa: E402
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
    parser.add_argument("--skip-labels", action="store_true")
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

    plan: ProjectPlan = parse_project_plan(text)
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

    if collisions:
        print(f"note: {collisions} label collision(s) were skipped — "
              f"see the LABELS report above", file=sys.stderr)
    return 1 if failures or collisions else 0


if __name__ == "__main__":
    sys.exit(main())
