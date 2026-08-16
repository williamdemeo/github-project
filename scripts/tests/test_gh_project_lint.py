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
        # Remove the LAST region's END: no later marker can mis-pair.
        broken = PLAN.replace("<!-- END GENERATED: unplanned -->\n", "")
        self.assertTrue(any("unterminated" in e for e in errors(broken)))

    def test_deleted_end_mid_file_reports_mismatch(self) -> None:
        # Removing an inner END makes its BEGIN close against the next
        # region's END — reported as mismatched markers.
        broken = PLAN.replace("<!-- END GENERATED: milestone-2 -->\n", "")
        self.assertTrue(any("mismatched" in e for e in errors(broken)))

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

    def test_created_issues_may_carry_github_side_labels(self) -> None:
        # An issue with a recorded (#N) already exists; its labels line
        # is state rendered back by update and may carry labels added on
        # GitHub (e.g. `wontfix`) — that must not block populate.
        updated = PLAN.replace(
            "### Issue M1-2: [M1-2] Write the docs (#42)\n\n"
            "**Labels:** `milestone-1-core`, `documentation`",
            "### Issue M1-2: [M1-2] Write the docs (#42)\n\n"
            "**Labels:** `milestone-1-core`, `documentation`, `wontfix`",
        )
        self.assertEqual(errors(updated), [])


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


class UnplannedRegionIsMirror(unittest.TestCase):
    def test_organic_headings_inside_region_are_not_flagged(self) -> None:
        mirrored = PLAN.replace(
            "_(no organically-filed issues — every issue on GitHub "
            "carries a `[MN-k]` planning prefix)_",
            "### Issue tracker went down twice (#12)\n\n"
            "**Labels:** `ops`\n\nOrganic body.\n\n"
            "### Milestone 3 retro notes (#13)\n\nMore organic body.\n",
        )
        self.assertEqual(errors(mirrored), [])

    def test_same_heading_outside_region_is_still_flagged(self) -> None:
        broken = PLAN.replace(
            "## How to use",
            "### Issue tracker went down twice (#12)\n\n## How to use",
        )
        self.assertTrue(any("malformed issue heading" in e
                            for e in errors(broken)))


class Advisories(unittest.TestCase):
    def test_missing_repository_is_warning(self) -> None:
        broken = PLAN.replace("**Repository**:  `octocat/demo`\n", "")
        self.assertEqual(errors(broken), [])
        self.assertTrue(any("no `**Repository**" in w for w in warnings(broken)))


class StructuralAmbiguities(unittest.TestCase):
    def test_duplicate_milestone_number_is_error(self) -> None:
        # ms_title_map keys on the number, so a duplicate makes
        # issue→milestone attachment ambiguous — populate must refuse.
        broken = PLAN.replace("### Milestone 2 — Polish", "### Milestone 1 — Polish")
        self.assertTrue(any("more than once" in e for e in errors(broken)))

    def test_malformed_issue_heading_is_error(self) -> None:
        # `M1-X` never parses, so the heading is invisible to populate
        # and would be erased when update rebuilds the region.
        broken = PLAN.replace(
            "### Issue M1-1: Set up the build",
            "### Issue M1-X: Set up the build",
        )
        self.assertTrue(any("malformed issue heading" in e for e in errors(broken)))

    def test_malformed_milestone_heading_is_error(self) -> None:
        # A hyphen instead of the em-dash parses to nothing; without
        # this check the plan would populate with no milestones at all.
        broken = PLAN.replace("### Milestone 1 — Core", "### Milestone 1 - Core")
        self.assertTrue(any("malformed milestone heading" in e
                            for e in errors(broken)))

    def test_milestone_checks_fire_even_when_no_heading_parses(self) -> None:
        # The consistency checks guard on the SECTION's presence: a
        # Milestones section whose every heading is malformed must not
        # disable them.
        broken = (PLAN
                  .replace("### Milestone 1 — Core", "### Milestone 1 - Core")
                  .replace("### Milestone 2 — Polish", "### Milestone 2 - Polish"))
        errs = errors(broken)
        self.assertTrue(any("refers to milestone" in e for e in errs))
        self.assertTrue(any("region 'milestone-1'" in e for e in errs))

    def test_backtickless_label_bullet_is_error(self) -> None:
        broken = PLAN.replace(
            "- `documentation` (0e8a16) — Documentation changes.",
            "- documentation (0e8a16) — Documentation changes.",
        )
        self.assertTrue(any("did not parse" in e for e in errors(broken)))


if __name__ == "__main__":
    unittest.main()
