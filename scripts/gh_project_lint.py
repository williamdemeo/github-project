#!/usr/bin/env python3
"""
File: scripts/gh_project_lint.py

Description:
  Structural validation of a project plan file — no network, no gh.

  Checks that the generated-region markers are balanced and uniquely
  named, the `## Labels` section parses (no duplicates or
  case/whitespace collisions), milestone references are consistent, and
  issue headings are well-formed.  This is the drift gate the
  populate/update design admits without network access: cheap enough to
  run on every PR (`make lint` / CI), and gh_project_populate.py runs
  the same checks before mutating anything.

Usage:
  python3 scripts/gh_project_lint.py docs/GITHUB_PROJECT.md

Exit codes:
  0  no errors (warnings may have been printed)
  1  at least one error
  2  the file could not be read
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Permit `python3 scripts/gh_project_lint.py ...` from the repo root by
# ensuring this file's directory is on sys.path before importing the
# sibling private modules.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _gh_project_lib import lint_plan, parse_project_plan  # noqa: E402
from _utils.file_ops import read_text  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the structure of a project plan file (no network)."
    )
    parser.add_argument("markdown", type=Path, help="Path to GITHUB_PROJECT.md.")
    args = parser.parse_args()

    read = read_text(args.markdown)
    if read.is_err:
        print(f"error: {read.unwrap_err()}", file=sys.stderr)
        return 2
    text = read.unwrap()

    problems = lint_plan(text)
    for p in problems:
        loc = f":{p.line}" if p.line else ""
        print(f"{args.markdown}{loc}: {p.severity}: {p.message}")

    errors = sum(1 for p in problems if p.severity == "error")
    warnings = len(problems) - errors
    plan = parse_project_plan(text)
    print(
        f"{args.markdown}: {len(plan.milestones)} milestones, "
        f"{len(plan.labels)} labels, {len(plan.issues)} issues — "
        f"{errors} error(s), {warnings} warning(s)"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
