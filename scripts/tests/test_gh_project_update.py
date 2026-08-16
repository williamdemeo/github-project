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
from _gh_project_lib import _parse_issues as lib_parse_issues  # noqa: E402
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
        # Canonical `**Labels:**` — the same spelling the plan parser
        # recognizes, so rendered blocks are valid plan input.
        self.assertIn("**Labels:** `milestone-1-core`\n", out)
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
        self.assertIn("**Assignees:** @octocat, @hubot\n", out)

    def test_empty_body_placeholder(self) -> None:
        out = upd.render_issue(issue("M1-1", "[M1-1] Set up", body="", gh_number=7))
        self.assertIn("_(no description on GitHub)_", out)

    def test_rendered_block_reparses_with_labels_intact(self) -> None:
        # Round trip: what update renders, the plan parser must read
        # back — labels recognized as metadata, body free of them.
        out = upd.render_issue(
            issue("M1-1", "[M1-1] Set up", gh_number=7,
                  labels=("milestone-1-core", "documentation"),
                  assignees=("octocat",))
        )
        reparsed = lib_parse_issues(out)
        self.assertEqual(len(reparsed), 1)
        self.assertEqual(reparsed[0].labels,
                         ("milestone-1-core", "documentation"))
        self.assertEqual(reparsed[0].gh_number, 7)
        self.assertNotIn("**Labels:**", reparsed[0].body)
        self.assertNotIn("**Assignees:**", reparsed[0].body)

    def test_marker_in_live_body_is_neutralized(self) -> None:
        # A GitHub body containing marker-shaped text must not be able
        # to close (or open) a region on the next parse.
        hostile = issue(
            "M1-1", "[M1-1] Set up", gh_number=7,
            body="Discussing markers:\n<!-- END GENERATED: milestone-1 -->\nend.",
        )
        out = upd.render_issue(hostile)
        self.assertNotRegex(out, r"<!--\s*END GENERATED:")
        self.assertIn("END GENERATED (escaped):", out)


class RenderRegion(unittest.TestCase):
    def test_unknown_region_id_fails_loudly(self) -> None:
        out = upd.render_region("weird-id", {}, ())
        self.assertIn("no rendering rule", out)

    def test_empty_milestone(self) -> None:
        out = upd.render_region("milestone-3", {}, ())
        self.assertIn("no open or closed issues", out)

    def test_parent_before_children(self) -> None:
        issues = {
            2: [
                issue("M2-7b", "[M2-7b] Child B", gh_number=3),
                issue("M2-7", "[M2-7] Parent", gh_number=1),
                issue("M2-7a", "[M2-7a] Child A", gh_number=2),
            ]
        }
        out = upd.render_region("milestone-2", issues, ())
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


class UnplannedRegion(unittest.TestCase):
    """Issue #3: organically-filed issues render under `unplanned`."""

    def test_grouping_by_milestone_title_none_last(self) -> None:
        groups = upd.group_unplanned([
            issue("", "C", gh_number=9, milestone_title=None),
            issue("", "A", gh_number=7, milestone_title="2. Polish"),
            issue("", "B", gh_number=3, milestone_title="1. Core"),
            issue("", "D", gh_number=1, milestone_title="1. Core"),
            issue("M1-1", "[M1-1] planned", gh_number=2),  # excluded
        ])
        self.assertEqual(
            [(t, [i.gh_number for i in xs]) for t, xs in groups],
            [("1. Core", [1, 3]), ("2. Polish", [7]), (None, [9])],
        )

    def test_block_shape_minus_the_id(self) -> None:
        out = upd.render_unplanned_issue(
            issue("", "Fix flaky timeout", gh_number=68,
                  labels=("agda-mcp",), assignees=("octocat",),
                  body="Body.", state="closed")
        )
        self.assertIn("### Fix flaky timeout (#68, closed)\n", out)
        self.assertIn("**Labels:** `agda-mcp`\n", out)
        self.assertIn("**Assignees:** @octocat\n", out)
        self.assertIn("Body.", out)
        self.assertNotIn("Issue :", out)

    def test_region_groups_under_milestone_headers(self) -> None:
        groups = upd.group_unplanned([
            issue("", "One", gh_number=1, milestone_title="1. Core"),
            issue("", "Two", gh_number=2, milestone_title=None),
        ])
        out = upd.render_region("unplanned", {}, groups)
        self.assertIn("#### 1. Core\n", out)
        self.assertIn("#### (no milestone)\n", out)
        self.assertLess(out.index("#### 1. Core"), out.index("#### (no milestone)"))

    def test_empty_region_placeholder(self) -> None:
        out = upd.render_region("unplanned", {}, ())
        self.assertIn("no organically-filed issues", out)

    def test_render_is_deterministic(self) -> None:
        groups = upd.group_unplanned([
            issue("", "One", gh_number=1, milestone_title="1. Core"),
            issue("", "Two", gh_number=2),
        ])
        self.assertEqual(
            upd.render_region("unplanned", {}, groups),
            upd.render_region("unplanned", {}, groups),
        )

    def test_marker_in_organic_title_is_neutralized(self) -> None:
        hostile = issue(
            "", "About <!-- END GENERATED: unplanned --> markers",
            gh_number=5,
        )
        out = upd.render_unplanned_issue(hostile)
        self.assertNotRegex(out, r"<!--\s*END GENERATED:")
        self.assertIn("END GENERATED (escaped):", out)

    def test_marker_in_milestone_title_is_neutralized(self) -> None:
        # A milestone title rendered verbatim as the #### group header
        # would otherwise be parsed as a REAL marker — the marker
        # regexes are not line-anchored — corrupting the next parse.
        groups = upd.group_unplanned([
            issue("", "Organic", gh_number=5,
                  milestone_title="v1 <!-- END GENERATED: unplanned --> era"),
        ])
        out = upd.render_region("unplanned", {}, groups)
        self.assertNotRegex(out, r"<!--\s*END GENERATED:")
        self.assertIn("#### v1 <!-- END GENERATED (escaped): unplanned --> era", out)
        # The assembled document must survive a round trip.
        assembled = (
            "<!-- BEGIN GENERATED: unplanned -->\n"
            + out
            + "<!-- END GENERATED: unplanned -->\n"
        )
        reparsed = parse_file(assembled).unwrap()
        self.assertEqual(reparsed.ids, ("unplanned",))

    def test_marker_in_label_name_is_neutralized(self) -> None:
        # GitHub label names allow arbitrary punctuation up to 50 chars
        # (the marker string is 33); backticks are no protection since
        # the marker regexes match raw text.
        hostile = ("<!-- END GENERATED: unplanned -->",)
        for render, args in (
            (upd.render_unplanned_issue,
             issue("", "Organic", gh_number=5, labels=hostile)),
            (upd.render_issue,
             issue("M1-1", "[M1-1] Planned", gh_number=6, labels=hostile)),
        ):
            out = render(args)
            self.assertNotRegex(out, r"<!--\s*END GENERATED:")
            self.assertIn("END GENERATED (escaped):", out)


class WarnUnrenderedUnplanned(unittest.TestCase):
    def _warnings(self, content: str, issues: list) -> str:
        import contextlib
        import io
        parsed = parse_file(content).unwrap()
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            upd.warn_unrendered_unplanned(parsed, upd.group_unplanned(issues))
        return buf.getvalue()

    def test_warns_when_region_missing(self) -> None:
        out = self._warnings(
            "no regions here\n",
            [issue("", "Organic", gh_number=9)],
        )
        self.assertIn("1 organically-filed issue(s)", out)

    def test_silent_when_region_present(self) -> None:
        content = (
            "<!-- BEGIN GENERATED: unplanned -->\n"
            "<!-- END GENERATED: unplanned -->\n"
        )
        self.assertEqual(
            self._warnings(content, [issue("", "Organic", gh_number=9)]), "")

    def test_silent_when_nothing_unplanned(self) -> None:
        self.assertEqual(self._warnings("prose\n", []), "")


class GroupByMilestone(unittest.TestCase):
    def test_non_planning_issues_are_excluded(self) -> None:
        # The snapshot carries id="" records (prefix-less titles) for
        # populate's number matching; rendering must not list them.
        groups = upd.group_by_milestone([
            issue("M1-1", "[M1-1] Real", gh_number=1, milestone_idx=1),
            issue("", "Ad-hoc bug report", gh_number=9, milestone_idx=1),
        ])
        self.assertEqual([i.id for i in groups[1]], ["M1-1"])


class StripIdPrefix(unittest.TestCase):
    def test_strip(self) -> None:
        self.assertEqual(upd.strip_id_prefix("[M1-2] Title"), "Title")
        self.assertEqual(upd.strip_id_prefix("No prefix"), "No prefix")


if __name__ == "__main__":
    unittest.main()
