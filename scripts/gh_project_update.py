#!/usr/bin/env python3
"""
File: scripts/gh_project_update.py

Description:

  Bring docs/GITHUB_PROJECT.md up to date: regenerate the issue listings
  inside it from current GitHub state, leaving hand-edited prose
  untouched.

  This is the GitHub → file direction; the inverse (file → GitHub) is
  gh_project_populate.py.  ("Update" names the intent — bring the file
  up to date — not a push to GitHub.)

  The file is treated as a sequence of manual prose segments interleaved
  with regions delimited by HTML-comment markers of the form

      <!-- BEGIN GENERATED: milestone-N -->
      ...
      <!-- END GENERATED: milestone-N -->

  Update preserves manual segments byte-for-byte and rebuilds each
  generated region from the live GitHub API.  A region with id
  `milestone-N` is rebuilt from issues whose `milestone-N-*` label
  identifies them as belonging to milestone N, ordered by their `[MN-k]`
  ordinal.  Region ids that don't match this pattern produce a
  no-rendering-rule placeholder so that bad ids fail loudly rather than
  silently emitting nothing.

Usage:

  python3 scripts/gh_project_update.py docs/GITHUB_PROJECT.md [OPTIONS]

  The target repository comes from the plan file's `**Repository**:`
  header, or from --repo (which wins when both are present).

  --check          Verify that the file already matches the rendered
                   output; do not write.

Exit codes follow diff(1), so a caller can tell "the plan is stale" from
"the check itself did not run":

  0  the file is current (or, without --check, was written successfully)
  1  --check only: the file differs from live GitHub state
  2  the run failed — authentication, API error, unreadable file, bad markers

  --no-env-prefix  Don't prefix `gh` with `env -u GH_TOKEN -u GITHUB_TOKEN`.
                   Needed wherever authentication comes *through* those
                   variables, GitHub Actions included: stripping them there
                   leaves gh with no credentials at all.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Permit `python3 scripts/gh_project_update.py ...` from the repo root
# by ensuring this file's directory is on sys.path before importing the
# sibling private modules.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _gh_project_lib import (  # noqa: E402
    GitHubClient,
    Issue,
    ParsedFile,
    parse_file,
    parse_repository,
)
from _utils import Result, PipelineError, ErrorType  # noqa: E402
from _utils.file_ops import read_text, write_text  # noqa: E402


# ── Issue grouping and sorting ───────────────────────────────────────────────

def group_by_milestone(issues: list[Issue]) -> dict[int, list[Issue]]:
    out: dict[int, list[Issue]] = {}
    for issue in issues:
        out.setdefault(issue.milestone_idx, []).append(issue)
    return out


def issue_sort_key(issue: Issue) -> tuple[int, int, str]:
    """Sort key for in-region ordering: (milestone, ordinal, suffix).
    The empty-string suffix on plain `[MN-k]` ids sorts before any
    alphabetic suffix, so a parent like `M2-7` precedes its children
    `M2-7a`, `M2-7b`, ... naturally.
    """
    m = re.match(r"^M(\d+)-(\d+)([a-z]?)$", issue.id)
    return (int(m.group(1)), int(m.group(2)), m.group(3) or "") if m else (10**9, 10**9, "")


# ── Region rendering ─────────────────────────────────────────────────────────

_ID_PREFIX_RE = re.compile(r"^\[M\d+-\d+[a-z]?\]\s+(.+)$")


def strip_id_prefix(title: str) -> str:
    """Drop the leading `[MN-k]` from an issue title; the heading reproduces
    the identifier separately, so leaving it in the title text is redundant."""
    m = _ID_PREFIX_RE.match(title)
    return m.group(1) if m else title


def render_issue(issue: Issue) -> str:
    title = strip_id_prefix(issue.title)
    state_suffix = ", closed" if issue.state == "closed" else ""
    ref = f"#{issue.gh_number}{state_suffix}" if issue.gh_number else "(no number)"
    labels = ", ".join(f"`{lbl}`" for lbl in issue.labels)
    # Assignees are GitHub-owned state (like open/closed); render them so
    # the file answers "who is on this?" without a browser.
    assignees = (
        f"**Assignees**: {', '.join('@' + a for a in issue.assignees)}\n\n"
        if issue.assignees else ""
    )
    body = issue.body.strip() if issue.body else "_(no description on GitHub)_"
    return (
        f"### Issue {issue.id}: {title} ({ref})\n"
        f"\n"
        f"**Labels**: {labels}\n"
        f"\n"
        f"{assignees}"
        f"{body}\n"
    )


def render_region(region_id: str, issues_by_ms: dict[int, list[Issue]]) -> str:
    m = re.match(r"^milestone-(\d+)$", region_id)
    if m is None:
        return f"\n<!-- region '{region_id}' has no rendering rule in gh_project_update.py -->\n"
    n = int(m.group(1))
    issues = issues_by_ms.get(n, [])
    if not issues:
        return f"\n_(no open or closed issues with `milestone-{n}-*` label)_\n"
    blocks = [render_issue(i) for i in sorted(issues, key=issue_sort_key)]
    return "\n" + "\n---\n\n".join(blocks) + "\n"


def assemble_file(parsed: ParsedFile, issues_by_ms: dict[int, list[Issue]]) -> str:
    parts: list[str] = []
    for i, manual in enumerate(parsed.manuals[:-1]):
        rid = parsed.ids[i]
        parts.append(manual)
        parts.append(f"<!-- BEGIN GENERATED: {rid} -->\n")
        parts.append(render_region(rid, issues_by_ms))
        parts.append(f"<!-- END GENERATED: {rid} -->")
    parts.append(parsed.manuals[-1])
    return "".join(parts)


# ── Validation ───────────────────────────────────────────────────────────────

def warn_orphan_milestones(parsed: ParsedFile, issues_by_ms: dict[int, list[Issue]]) -> None:
    """Stderr-warn for each milestone-N-* label group lacking a marker
    region in the file.  Such issues are silently dropped from output;
    surfacing the warning lets the maintainer notice and add the region."""
    region_milestones: set[int] = set()
    for rid in parsed.ids:
        m = re.match(r"^milestone-(\d+)$", rid)
        if m is not None:
            region_milestones.add(int(m.group(1)))
    for n, issues in sorted(issues_by_ms.items()):
        if n not in region_milestones:
            print(
                f"warning: {len(issues)} issue(s) with milestone-{n}-* label "
                f"have no <!-- BEGIN GENERATED: milestone-{n} --> region",
                file=sys.stderr,
            )


# ── Main ─────────────────────────────────────────────────────────────────────

def _render(parsed: ParsedFile, issues: list[Issue]) -> str:
    issues_by_ms = group_by_milestone(issues)
    warn_orphan_milestones(parsed, issues_by_ms)
    return assemble_file(parsed, issues_by_ms)


def run(markdown: Path, repo: str | None, env_prefix: bool) -> Result[str, PipelineError]:
    """Compose read → parse → fetch → render; returns the new content."""
    def render_with_repo(text: str) -> Result[str, PipelineError]:
        target = repo or parse_repository(text)
        if target is None:
            return Result.err(PipelineError(
                error_type=ErrorType.INVALID_CONFIG,
                message=(
                    "no repository given — add a `**Repository**: owner/name` "
                    "header to the plan file or pass --repo"
                ),
            ))
        client = GitHubClient(repo=target, env_prefix=env_prefix)
        return parse_file(text).and_then(
            lambda parsed: client.list_issues().map(
                lambda issues: _render(parsed, issues)
            )
        )

    return read_text(markdown).and_then(render_with_repo)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate the generated regions of GITHUB_PROJECT.md from GitHub.",
    )
    parser.add_argument("markdown", type=Path, help="Path to GITHUB_PROJECT.md.")
    parser.add_argument("--repo", help="GitHub repo (OWNER/NAME); overrides the file header.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify that the file already matches the rendered output; do not write.",
    )
    parser.add_argument(
        "--no-env-prefix",
        action="store_true",
        help="Don't prefix `gh` with `env -u GH_TOKEN -u GITHUB_TOKEN`.",
    )
    args = parser.parse_args()

    result = run(args.markdown, args.repo, env_prefix=not args.no_env_prefix)
    if result.is_err:
        print(f"update failed: {result.unwrap_err()}", file=sys.stderr)
        return 2
    rendered = result.unwrap()

    if args.check:
        existing = args.markdown.read_text(encoding="utf-8")
        if existing == rendered:
            print(f"OK: {args.markdown} matches rendered output")
            return 0
        print(f"FAIL: {args.markdown} differs from rendered output", file=sys.stderr)
        print("       (run without --check to update in place)", file=sys.stderr)
        return 1

    write_result = write_text(args.markdown, rendered)
    if write_result.is_err:
        print(f"write failed: {write_result.unwrap_err()}", file=sys.stderr)
        return 2
    print(f"wrote {args.markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
