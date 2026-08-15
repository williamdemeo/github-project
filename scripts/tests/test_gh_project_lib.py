"""
File: scripts/tests/test_gh_project_lib.py

Description:
  Unit tests for the pure core of _gh_project_lib: plan-file parsing,
  generated-region parsing, label/milestone/issue planning, and the
  crash-safe issue-number write-back.  No network, no gh.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _gh_project_lib as lib  # noqa: E402
from _gh_project_lib import (  # noqa: E402
    Issue,
    Label,
    Milestone,
    normalize_label_name,
    parse_file,
    parse_issue_id,
    parse_project_plan,
    parse_repository,
    plan_issues,
    plan_labels,
    plan_milestones,
    record_issue_number,
)

# A miniature plan file exercising every construct the parser must
# handle: repository header, labels section, two milestones, issues with
# metadata lines, a recorded issue number, a fan-out letter suffix, a
# region marker between issues, and trailing prose after the last issue.
PLAN = """\
# Demo — GitHub Project Roadmap

**Repository**:  `octocat/demo`

## Summary

Two milestones, four issues.

## Labels

- `milestone-1-core` (0075ca) — Milestone 1: Core.
- `milestone-2-polish` (5319e7) — Milestone 2: Polish.
- `documentation` (0e8a16) — Documentation changes.

## Milestones

### Milestone 1 — Core

**Description:**

Build the core.

**Exit criterion:**

Core works.

---

### Milestone 2 — Polish

**Description:**

Polish it.

**Exit criterion:**

It shines.

## Issues

## Milestone 1 — Core

<!-- BEGIN GENERATED: milestone-1 -->

### Issue M1-1: Set up the build

**Labels:** `milestone-1-core`
**Milestone:** 1. Core

Body of M1-1.

## Acceptance criteria

- CI is green on a fresh clone.

---

### Issue M1-2: [M1-2] Write the docs (#42)

**Labels:** `milestone-1-core`, `documentation`

Body of M1-2.

<!-- END GENERATED: milestone-1 -->

## Milestone 2 — Polish

<!-- BEGIN GENERATED: milestone-2 -->

### Issue M2-1: Shine it

**Labels:** `milestone-2-polish`

Body of M2-1.

---

### Issue M2-1a: Shine it more

**Labels:** `milestone-2-polish`

Body of M2-1a.

<!-- END GENERATED: milestone-2 -->

## How to use

Trailing prose that must not leak into any issue body.
"""


class ParseIssueId(unittest.TestCase):
    def test_plain(self) -> None:
        self.assertEqual(
            parse_issue_id("[M1-2] Title here"),
            ("M1-2", 1, 2, "", "Title here"),
        )

    def test_letter_suffix(self) -> None:
        self.assertEqual(
            parse_issue_id("[M2-7a] Child ticket"),
            ("M2-7a", 2, 7, "a", "Child ticket"),
        )

    def test_no_space_after_bracket_is_rejected(self) -> None:
        self.assertIsNone(parse_issue_id("[M1-1]Title"))

    def test_unprefixed_is_rejected(self) -> None:
        self.assertIsNone(parse_issue_id("Ordinary bug report"))


class IssueIdOrdering(unittest.TestCase):
    def test_ordering(self) -> None:
        self.assertTrue(lib.issue_id_gte("M1-3", "M1-2"))
        self.assertFalse(lib.issue_id_gte("M0-9", "M1-1"))
        self.assertTrue(lib.issue_id_gte("M2-7a", "M2-7"))
        self.assertTrue(lib.issue_id_gte("M2-7b", "M2-7a"))


class ParseRepository(unittest.TestCase):
    def test_backticked(self) -> None:
        self.assertEqual(parse_repository(PLAN), "octocat/demo")

    def test_bare(self) -> None:
        self.assertEqual(
            parse_repository("**Repository**: octocat/demo\n"), "octocat/demo"
        )

    def test_absent(self) -> None:
        self.assertIsNone(parse_repository("# No header here\n"))


class ParsePlan(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = parse_project_plan(PLAN)

    def test_milestones(self) -> None:
        self.assertEqual(
            [(m.number, m.title) for m in self.plan.milestones],
            [(1, "1. Core"), (2, "2. Polish")],
        )
        self.assertIn("Build the core.", self.plan.milestones[0].description)
        self.assertIn(
            "**Exit criterion:** Core works.", self.plan.milestones[0].description
        )

    def test_labels_from_explicit_section(self) -> None:
        self.assertEqual(
            [(l.name, l.color) for l in self.plan.labels],
            [
                ("milestone-1-core", "0075ca"),
                ("milestone-2-polish", "5319e7"),
                ("documentation", "0e8a16"),
            ],
        )

    def test_labels_fallback_collects_from_issues(self) -> None:
        no_section = PLAN.replace("## Labels", "## Labelless")
        labels = parse_project_plan(no_section).labels
        self.assertEqual(
            {l.name for l in labels},
            {"milestone-1-core", "milestone-2-polish", "documentation"},
        )
        self.assertTrue(all(l.color == "cccccc" for l in labels))

    def test_labels_no_defaults_imposed(self) -> None:
        self.assertEqual(parse_project_plan("# Empty plan\n").labels, ())

    def test_issue_ids_and_titles(self) -> None:
        issues = {i.id: i for i in self.plan.issues}
        self.assertEqual(
            set(issues), {"M1-1", "M1-2", "M2-1", "M2-1a"}
        )
        # Every pushed title carries exactly one [MN-k] prefix, whether or
        # not the heading already had one.
        self.assertEqual(issues["M1-1"].title, "[M1-1] Set up the build")
        self.assertEqual(issues["M1-2"].title, "[M1-2] Write the docs")

    def test_recorded_number_is_state_not_title(self) -> None:
        m12 = next(i for i in self.plan.issues if i.id == "M1-2")
        self.assertEqual(m12.gh_number, 42)
        self.assertNotIn("(#42)", m12.title)

    def test_metadata_lines_are_stripped_from_bodies(self) -> None:
        for issue in self.plan.issues:
            self.assertNotIn("**Labels:**", issue.body)
            self.assertNotIn("**Milestone:**", issue.body)

    def test_labels_parsed_from_metadata(self) -> None:
        m12 = next(i for i in self.plan.issues if i.id == "M1-2")
        self.assertEqual(m12.labels, ("milestone-1-core", "documentation"))

    def test_bodies_do_not_leak(self) -> None:
        bodies = {i.id: i.body for i in self.plan.issues}
        # `## ` sections are legitimate BODY content (real plans use
        # `## Tasks` / `## Acceptance criteria` inside issues) — bounding
        # on generic headings would truncate them.
        self.assertEqual(
            bodies["M1-1"],
            "Body of M1-1.\n\n## Acceptance criteria\n\n"
            "- CI is green on a fresh clone.",
        )
        self.assertEqual(bodies["M1-2"], "Body of M1-2.")
        self.assertEqual(bodies["M2-1"], "Body of M2-1.")
        # The last issue's body must not swallow the trailing prose or
        # the closing region marker.
        self.assertEqual(bodies["M2-1a"], "Body of M2-1a.")

    def test_milestone_idx_from_id(self) -> None:
        self.assertEqual(
            {i.id: i.milestone_idx for i in self.plan.issues},
            {"M1-1": 1, "M1-2": 1, "M2-1": 2, "M2-1a": 2},
        )


class RecordIssueNumber(unittest.TestCase):
    def test_adds_suffix(self) -> None:
        text, found = record_issue_number(PLAN, "M1-1", 7)
        self.assertTrue(found)
        self.assertIn("### Issue M1-1: Set up the build (#7)\n", text)

    def test_replaces_existing_suffix(self) -> None:
        text, found = record_issue_number(PLAN, "M1-2", 99)
        self.assertTrue(found)
        self.assertIn("### Issue M1-2: [M1-2] Write the docs (#99)\n", text)
        self.assertNotIn("(#42)", text)

    def test_only_the_heading_changes(self) -> None:
        text, _ = record_issue_number(PLAN, "M1-1", 7)
        self.assertEqual(
            [ln for ln in PLAN.splitlines() if "M1-1" in ln and ln.startswith("###")],
            ["### Issue M1-1: Set up the build"],
        )
        # Remove the one changed line from both; the rest is untouched.
        before = PLAN.replace("### Issue M1-1: Set up the build\n", "")
        after = text.replace("### Issue M1-1: Set up the build (#7)\n", "")
        self.assertEqual(before, after)

    def test_unknown_id_reports_not_found(self) -> None:
        text, found = record_issue_number(PLAN, "M9-9", 1)
        self.assertFalse(found)
        self.assertEqual(text, PLAN)

    def test_id_match_is_exact_not_prefix(self) -> None:
        # Recording M2-1 must not touch the M2-1a heading.
        text, found = record_issue_number(PLAN, "M2-1", 5)
        self.assertTrue(found)
        self.assertIn("### Issue M2-1: Shine it (#5)\n", text)
        self.assertIn("### Issue M2-1a: Shine it more\n", text)


class ParseFileRegions(unittest.TestCase):
    def test_round_trip_structure(self) -> None:
        parsed = parse_file(PLAN).unwrap()
        self.assertEqual(parsed.ids, ("milestone-1", "milestone-2"))
        self.assertEqual(len(parsed.manuals), 3)
        self.assertTrue(parsed.manuals[-1].startswith("\n\n## How to use"))

    def test_unterminated_begin(self) -> None:
        r = parse_file("<!-- BEGIN GENERATED: x -->\nno end\n")
        self.assertTrue(r.is_err)
        self.assertIn("unterminated", r.unwrap_err().message)

    def test_mismatched_ids(self) -> None:
        r = parse_file(
            "<!-- BEGIN GENERATED: x -->\n<!-- END GENERATED: y -->\n"
        )
        self.assertTrue(r.is_err)
        self.assertIn("mismatched", r.unwrap_err().message)

    def test_stray_end(self) -> None:
        r = parse_file("prose\n<!-- END GENERATED: x -->\n")
        self.assertTrue(r.is_err)
        self.assertIn("no matching BEGIN", r.unwrap_err().message)

    def test_stray_end_before_a_valid_region(self) -> None:
        # A stray END must be caught even when a well-formed region
        # follows it — not only at end of file.
        r = parse_file(
            "<!-- END GENERATED: stray -->\n"
            "<!-- BEGIN GENERATED: ok -->\n<!-- END GENERATED: ok -->\n"
        )
        self.assertTrue(r.is_err)
        self.assertIn("no matching BEGIN", r.unwrap_err().message)
        self.assertIn("stray", r.unwrap_err().message)

    def test_nested_begin(self) -> None:
        r = parse_file(
            "<!-- BEGIN GENERATED: x -->\n"
            "<!-- BEGIN GENERATED: y -->\n"
            "<!-- END GENERATED: x -->\n"
        )
        self.assertTrue(r.is_err)
        self.assertIn("nested", r.unwrap_err().message)


class PlanLabels(unittest.TestCase):
    def test_reconciliation(self) -> None:
        desired = (
            Label("bug", "ff0000", "A bug"),
            Label("era: conway", "00ff00", "Conway era"),
            Label("Docs", "0000ff", "Documentation"),
            Label("brand-new", "cccccc", ""),
        )
        existing = (
            Label("bug", "aa0000", "Bugs (different color)"),
            Label("era:conway", "00ff00", "Conway era"),
            Label("docs", "0000ff", "Documentation"),
        )
        plan = plan_labels(desired, existing)
        self.assertEqual([l.name for l in plan.to_create], ["brand-new"])
        self.assertEqual([(d.name, e.name) for d, e in plan.existing], [("bug", "bug")])
        self.assertEqual(
            [(d.name, e.name) for d, e in plan.collisions],
            [("era: conway", "era:conway"), ("Docs", "docs")],
        )

    def test_normalize(self) -> None:
        self.assertEqual(normalize_label_name("Era: Conway"), "era:conway")
        self.assertEqual(normalize_label_name("good first issue"),
                         normalize_label_name("Good  First Issue"))


class PlanMilestones(unittest.TestCase):
    def test_match_by_title(self) -> None:
        desired = (
            Milestone(1, "1. Core", "d1"),
            Milestone(2, "2. Polish", "d2"),
        )
        existing = (Milestone(1, "1. Core", "live", gh_number=11),)
        plan = plan_milestones(desired, existing)
        self.assertEqual([m.title for m in plan.to_create], ["2. Polish"])
        self.assertEqual([(m.title, m.gh_number) for m in plan.existing],
                         [("1. Core", 11)])


class PlanIssues(unittest.TestCase):
    def test_match_by_id_prefix(self) -> None:
        desired = (Issue("M1-1", "[M1-1] A", "b"),)
        existing = (Issue("M1-1", "[M1-1] A renamed", "b", gh_number=3),)
        plan = plan_issues(desired, existing)
        self.assertEqual(plan.to_create, ())
        self.assertEqual(plan.existing[0][1].gh_number, 3)

    def test_match_by_recorded_number(self) -> None:
        # Someone stripped the [M1-1] prefix on GitHub.  The snapshot
        # carries such issues with id="" (the parser must not drop them,
        # or this fallback would be unreachable); the recorded (#3) in
        # the plan file prevents a duplicate.
        desired = (Issue("M1-1", "[M1-1] A", "b", gh_number=3),)
        existing = (Issue("", "A, prefix stripped", "b", gh_number=3),)
        plan = plan_issues(desired, existing)
        self.assertEqual(plan.to_create, ())
        self.assertEqual(plan.existing[0][1].gh_number, 3)

    def test_blank_ids_never_match_each_other(self) -> None:
        # Two different prefix-less live issues must not collapse into
        # one by their empty id.
        desired = (Issue("M1-1", "[M1-1] A", "b"),)
        existing = (
            Issue("", "Ad-hoc bug", "b", gh_number=8),
            Issue("", "Another ad-hoc", "b", gh_number=9),
        )
        plan = plan_issues(desired, existing)
        self.assertEqual([i.id for i in plan.to_create], ["M1-1"])

    def test_new_issue(self) -> None:
        desired = (Issue("M1-2", "[M1-2] New", "b"),)
        existing = (Issue("M1-1", "[M1-1] Old", "b", gh_number=3),)
        plan = plan_issues(desired, existing)
        self.assertEqual([i.id for i in plan.to_create], ["M1-2"])


class ParseJsonPagination(unittest.TestCase):
    """`gh api --paginate` concatenates pages as separate JSON documents;
    the decoder must consume them all, not fail with 'Extra data'."""

    def test_multiple_documents_are_concatenated(self) -> None:
        r = lib._parse_json('[{"a": 1}, {"a": 2}][{"a": 3}]', "things")
        self.assertEqual(r.unwrap(), [{"a": 1}, {"a": 2}, {"a": 3}])

    def test_newline_separated_documents(self) -> None:
        r = lib._parse_json('[{"a": 1}]\n[{"a": 2}]\n', "things")
        self.assertEqual(r.unwrap(), [{"a": 1}, {"a": 2}])

    def test_single_document_still_works(self) -> None:
        self.assertEqual(lib._parse_json('[{"a": 1}]', "things").unwrap(),
                         [{"a": 1}])

    def test_empty_input_is_empty_list(self) -> None:
        self.assertEqual(lib._parse_json("", "things").unwrap(), [])

    def test_non_list_document_is_err(self) -> None:
        self.assertTrue(lib._parse_json('{"a": 1}', "things").is_err)

    def test_garbage_is_err(self) -> None:
        self.assertTrue(lib._parse_json('[1] not json', "things").is_err)


class ParseIssuesJson(unittest.TestCase):
    """The snapshot parser must keep prefix-less issues and drop PRs."""

    def test_prefixless_issue_kept_with_blank_id(self) -> None:
        payload = json.dumps([
            {"number": 7, "title": "No prefix here", "body": "b",
             "labels": [{"name": "milestone-1-core"}], "milestone": None,
             "state": "open", "assignees": []},
        ])
        issues = lib._parse_issues_json(payload).unwrap()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].id, "")
        self.assertEqual(issues[0].gh_number, 7)
        self.assertEqual(issues[0].milestone_idx, 1)  # from the label

    def test_pull_requests_are_filtered(self) -> None:
        payload = json.dumps([
            {"number": 5, "title": "[M1-1] Real issue", "body": "",
             "labels": [], "milestone": None, "state": "open",
             "assignees": []},
            {"number": 6, "title": "[M1-2] Actually a PR", "body": "",
             "labels": [], "milestone": None, "state": "open",
             "assignees": [], "pull_request": {"url": "..."}},
        ])
        issues = lib._parse_issues_json(payload).unwrap()
        self.assertEqual([i.gh_number for i in issues], [5])


if __name__ == "__main__":
    unittest.main()
