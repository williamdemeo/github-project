"""
File: scripts/tests/test_gh_project_update.py

Description:
  Tests for gh_project_update's pure rendering core: issue formatting,
  region rebuilding, and manual-prose preservation.  No network.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gh_project_update as upd  # noqa: E402
from _gh_project_lib import Issue, parse_file  # noqa: E402
from test_gh_project_lib import PLAN  # noqa: E402


def issue(id_: str, title: str, **kw) -> Issue:
    return Issue(id=id_, title=title, body=kw.pop("body", "Body."), **kw)


class RenderIssue(unittest.TestCase):
    def test_open_issue(self) -> None:
        out = upd.render_issue(
            issue("M1-1", "[M1-1] Set up", gh_number=7,
                  labels=("milestone-1-core",))
        )
        self.assertIn("### Issue M1-1: Set up (#7)\n", out)
        self.assertIn("**Labels**: `milestone-1-core`\n", out)
        self.assertNotIn("closed", out)
        self.assertNotIn("Assignees", out)

    def test_closed_issue(self) -> None:
        out = upd.render_issue(
            issue("M1-1", "[M1-1] Set up", gh_number=7, state="closed")
        )
        self.assertIn("(#7, closed)", out)

    def test_assignees_rendered_when_present(self) -> None:
        out = upd.render_issue(
            issue("M1-1", "[M1-1] Set up", gh_number=7,
                  assignees=("octocat", "hubot"))
        )
        self.assertIn("**Assignees**: @octocat, @hubot\n", out)

    def test_empty_body_placeholder(self) -> None:
        out = upd.render_issue(issue("M1-1", "[M1-1] Set up", body="", gh_number=7))
        self.assertIn("_(no description on GitHub)_", out)


class RenderRegion(unittest.TestCase):
    def test_unknown_region_id_fails_loudly(self) -> None:
        out = upd.render_region("weird-id", {})
        self.assertIn("no rendering rule", out)

    def test_empty_milestone(self) -> None:
        out = upd.render_region("milestone-3", {})
        self.assertIn("no open or closed issues", out)

    def test_parent_before_children(self) -> None:
        issues = {
            2: [
                issue("M2-7b", "[M2-7b] Child B", gh_number=3),
                issue("M2-7", "[M2-7] Parent", gh_number=1),
                issue("M2-7a", "[M2-7a] Child A", gh_number=2),
            ]
        }
        out = upd.render_region("milestone-2", issues)
        self.assertLess(out.index("Parent"), out.index("Child A"))
        self.assertLess(out.index("Child A"), out.index("Child B"))


class AssembleFile(unittest.TestCase):
    def test_manuals_preserved_and_regions_rebuilt(self) -> None:
        parsed = parse_file(PLAN).unwrap()
        issues = {
            1: [issue("M1-1", "[M1-1] Set up the build", gh_number=1,
                      labels=("milestone-1-core",))],
            2: [issue("M2-1", "[M2-1] Shine it", gh_number=3, state="closed")],
        }
        out = upd.assemble_file(parsed, issues)
        # Manual prose survives byte-for-byte.
        for segment in parsed.manuals:
            self.assertIn(segment, out)
        # Regions were rebuilt from the fake GitHub state.
        self.assertIn("### Issue M1-1: Set up the build (#1)", out)
        self.assertIn("### Issue M2-1: Shine it (#3, closed)", out)
        # Round trip: the output parses to the same structure.
        reparsed = parse_file(out).unwrap()
        self.assertEqual(reparsed.ids, parsed.ids)
        self.assertEqual(reparsed.manuals, parsed.manuals)


class StripIdPrefix(unittest.TestCase):
    def test_strip(self) -> None:
        self.assertEqual(upd.strip_id_prefix("[M1-2] Title"), "Title")
        self.assertEqual(upd.strip_id_prefix("No prefix"), "No prefix")


if __name__ == "__main__":
    unittest.main()
