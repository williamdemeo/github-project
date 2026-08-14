"""
File: scripts/tests/test_gh_project_lint.py

Description:
  Tests for the pure structural lint (lint_plan) that `make lint` and
  gh_project_populate.py share.  No network.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _gh_project_lib import lint_plan  # noqa: E402
from test_gh_project_lib import PLAN  # noqa: E402


def errors(text: str) -> list[str]:
    return [p.message for p in lint_plan(text) if p.severity == "error"]


def warnings(text: str) -> list[str]:
    return [p.message for p in lint_plan(text) if p.severity == "warning"]


class CleanPlan(unittest.TestCase):
    def test_fixture_is_clean(self) -> None:
        self.assertEqual(errors(PLAN), [])
        self.assertEqual(warnings(PLAN), [])


class MarkerProblems(unittest.TestCase):
    def test_unterminated(self) -> None:
        broken = PLAN.replace("<!-- END GENERATED: milestone-2 -->\n", "")
        self.assertTrue(any("unterminated" in e for e in errors(broken)))

    def test_mismatched(self) -> None:
        broken = PLAN.replace(
            "<!-- END GENERATED: milestone-2 -->",
            "<!-- END GENERATED: milestone-3 -->",
        )
        self.assertTrue(any("mismatched" in e for e in errors(broken)))

    def test_duplicate_region_id(self) -> None:
        broken = PLAN.replace(
            "<!-- BEGIN GENERATED: milestone-2 -->",
            "<!-- BEGIN GENERATED: milestone-1 -->",
        ).replace(
            "<!-- END GENERATED: milestone-2 -->",
            "<!-- END GENERATED: milestone-1 -->",
        )
        self.assertTrue(any("duplicate generated-region id" in e
                            for e in errors(broken)))


class ReferenceProblems(unittest.TestCase):
    def test_duplicate_issue_id(self) -> None:
        broken = PLAN.replace(
            "### Issue M2-1a: Shine it more",
            "### Issue M2-1: Shine it more",
        )
        self.assertTrue(any("duplicate issue ID M2-1" in e for e in errors(broken)))

    def test_issue_with_unknown_milestone(self) -> None:
        broken = PLAN.replace(
            "### Issue M2-1a: Shine it more",
            "### Issue M9-1: Shine it more",
        )
        self.assertTrue(any("milestone 9" in e for e in errors(broken)))

    def test_region_with_unknown_milestone(self) -> None:
        broken = PLAN.replace(
            "<!-- BEGIN GENERATED: milestone-2 -->",
            "<!-- BEGIN GENERATED: milestone-9 -->",
        ).replace(
            "<!-- END GENERATED: milestone-2 -->",
            "<!-- END GENERATED: milestone-9 -->",
        )
        self.assertTrue(any("region 'milestone-9'" in e for e in errors(broken)))

    def test_issue_references_unlisted_label(self) -> None:
        broken = PLAN.replace(
            "**Labels:** `milestone-2-polish`\n\nBody of M2-1a.",
            "**Labels:** `nonexistent-label`\n\nBody of M2-1a.",
        )
        self.assertTrue(any("`nonexistent-label`" in e for e in errors(broken)))


class LabelSectionProblems(unittest.TestCase):
    def test_malformed_entry(self) -> None:
        broken = PLAN.replace(
            "- `documentation` (0e8a16) — Documentation changes.",
            "- `documentation` (ZZZZZZ) — Bad color.",
        )
        self.assertTrue(any("did not parse" in e for e in errors(broken)))

    def test_case_collision_within_plan(self) -> None:
        broken = PLAN.replace(
            "- `documentation` (0e8a16) — Documentation changes.",
            "- `documentation` (0e8a16) — Documentation changes.\n"
            "- `Documentation` (0e8a16) — Duplicate modulo case.",
        )
        self.assertTrue(any("collide after" in e for e in errors(broken)))

    def test_exact_duplicate(self) -> None:
        broken = PLAN.replace(
            "- `documentation` (0e8a16) — Documentation changes.",
            "- `documentation` (0e8a16) — Documentation changes.\n"
            "- `documentation` (0e8a16) — Same again.",
        )
        self.assertTrue(any("duplicate label `documentation`" in e
                            for e in errors(broken)))


class Advisories(unittest.TestCase):
    def test_missing_repository_is_warning(self) -> None:
        broken = PLAN.replace("**Repository**:  `octocat/demo`\n", "")
        self.assertEqual(errors(broken), [])
        self.assertTrue(any("no `**Repository**" in w for w in warnings(broken)))

    def test_duplicate_milestone_number_is_warning(self) -> None:
        broken = PLAN.replace("### Milestone 2 — Polish", "### Milestone 1 — Polish")
        self.assertTrue(any("more than once" in w for w in warnings(broken)))


if __name__ == "__main__":
    unittest.main()
