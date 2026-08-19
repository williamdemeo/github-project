"""
File: scripts/tests/test_integration.py

Description:
  End-to-end tests of gh_project_populate.py and gh_project_update.py
  against the recorded fake `gh` in scripts/tests/fake_gh/ — the real
  scripts run as subprocesses with the fake on PATH, so command
  construction, JSON parsing, write-back, and exit codes are all
  exercised with zero network.

  Pinned here, per ualib/agda-algebras#293 item 6 and the design
  requirements:
    - populate fetches ONE snapshot per run (one `label list`, one
      milestones GET, one `issue list`) no matter how many issues the
      plan holds;
    - a rerun after success creates nothing (idempotency);
    - a rerun after a mid-run crash creates only what is missing
      (crash-safe write-back);
    - label near-collisions are reported, not created;
    - update rewrites only the generated regions and follows diff(1)
      exit codes for --check.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_gh_project_lib import PLAN  # noqa: E402

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
FAKE_GH_DIR = TESTS_DIR / "fake_gh"


class FakeGhHarness(unittest.TestCase):
    """Shared setup: a temp plan file and a fresh fake-gh state dir."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.state = tmp / "state"
        self.state.mkdir()
        self.plan_path = tmp / "GITHUB_PROJECT.md"
        self.plan_path.write_text(PLAN, encoding="utf-8")

    def run_script(self, script: str, *args: str) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "PATH": f"{FAKE_GH_DIR}:{os.environ['PATH']}",
            "FAKE_GH_STATE": str(self.state),
        }
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / script), str(self.plan_path), *args],
            env=env, capture_output=True, text=True, timeout=120,
        )

    def populate(self, *args: str) -> subprocess.CompletedProcess:
        return self.run_script(
            "gh_project_populate.py", "--yes", "--delay", "0", *args
        )

    def update(self, *args: str) -> subprocess.CompletedProcess:
        return self.run_script("gh_project_update.py", *args)

    def calls(self, needle: str) -> int:
        log = self.state / "calls.log"
        if not log.exists():
            return 0
        return sum(needle in line for line in log.read_text().splitlines())

    def issues_on_fake_github(self) -> list[dict]:
        path = self.state / "issues.json"
        return json.loads(path.read_text()) if path.exists() else []


class FreshPopulate(FakeGhHarness):
    def test_creates_everything_with_one_snapshot(self) -> None:
        proc = self.populate()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

        # One snapshot per run — not one list call per created entity
        # (#293 item 6: this used to be 1 ListIssues per issue create).
        self.assertEqual(self.calls("octocat/demo/labels"), 1)
        self.assertEqual(self.calls("octocat/demo/issues"), 1)
        self.assertEqual(
            sum("milestones" in line and "POST" not in line
                for line in (self.state / "calls.log").read_text().splitlines()),
            1,
        )

        created = self.issues_on_fake_github()
        self.assertEqual(len(created), 4)
        self.assertEqual(
            sorted(i["title"] for i in created),
            [
                "[M1-1] Set up the build",
                "[M1-2] Write the docs",
                "[M2-1] Shine it",
                "[M2-1a] Shine it more",
            ],
        )
        # Milestones attached by title.
        m11 = next(i for i in created if i["title"].startswith("[M1-1]"))
        self.assertEqual(m11["milestone"], {"title": "1. Core"})

        # Every heading now records its number (crash-safe write-back),
        # including replacing the stale (#42) the fixture carried.
        text = self.plan_path.read_text(encoding="utf-8")
        for issue in created:
            issue_id = issue["title"].split("]")[0][1:]
            self.assertIn(f"### Issue {issue_id}: ", text)
            self.assertIn(f"(#{issue['number']})", text)
        self.assertNotIn("(#42)", text)

    def test_rerun_creates_nothing(self) -> None:
        first = self.populate()
        self.assertEqual(first.returncode, 0, first.stderr)
        creates_after_first = self.calls("issue create")

        second = self.populate()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.calls("issue create"), creates_after_first)
        self.assertEqual(len(self.issues_on_fake_github()), 4)
        self.assertIn("- exists: issue", second.stdout)

    def test_dry_run_mutates_nothing(self) -> None:
        proc = self.populate("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("+ create issue: [M1-1] Set up the build", proc.stdout)
        self.assertEqual(self.calls("issue create"), 0)
        self.assertEqual(self.calls("label create"), 0)
        self.assertEqual(self.issues_on_fake_github(), [])
        self.assertEqual(self.plan_path.read_text(encoding="utf-8"), PLAN)


class CrashSafety(FakeGhHarness):
    def test_interrupted_run_reruns_without_duplicates(self) -> None:
        # The third issue-create call dies mid-run.  Populate continues
        # past a failed creation (a single bad issue must not abort the
        # rest), so the run ends with 3 of 4 issues live and exit 1.
        (self.state / "fail_create_issue_at").write_text("3")
        first = self.populate()
        self.assertEqual(first.returncode, 1, first.stdout + first.stderr)
        self.assertEqual(len(self.issues_on_fake_github()), 3)

        # Every number GitHub handed out was persisted immediately —
        # BEFORE the next mutation — so all 3 are already on disk.
        text = self.plan_path.read_text(encoding="utf-8")
        self.assertEqual(text.count("(#"), 3)

        # Heal the fake and rerun: exactly the one missing issue is
        # created; nothing that succeeded before is duplicated.
        (self.state / "fail_create_issue_at").unlink()
        second = self.populate()
        self.assertEqual(second.returncode, 0, second.stderr)
        titles = [i["title"] for i in self.issues_on_fake_github()]
        self.assertEqual(len(titles), 4)
        self.assertEqual(len(set(titles)), 4, f"duplicates: {titles}")


class TitleEditResilience(FakeGhHarness):
    def test_stripped_prefix_does_not_duplicate(self) -> None:
        # After a successful populate, someone edits an issue title on
        # GitHub and removes its [MN-k] prefix.  The recorded (#N) in
        # the plan file is the fallback join key: a re-run must match
        # the renamed issue by number, not re-create it.
        self.assertEqual(self.populate().returncode, 0)
        issues = self.issues_on_fake_github()
        victim = next(i for i in issues if i["title"].startswith("[M1-1]"))
        victim["title"] = "Storage layer, renamed with no prefix"
        (self.state / "issues.json").write_text(json.dumps(issues))

        rerun = self.populate()
        self.assertEqual(rerun.returncode, 0, rerun.stderr + rerun.stdout)
        self.assertEqual(len(self.issues_on_fake_github()), 4)
        self.assertIn(f"- exists: issue #{victim['number']}", rerun.stdout)

        # Update still works: the prefix-less issue leaves its milestone
        # region (no stable identifier to render under) and surfaces in
        # the unplanned region instead — visible, not vanished.
        upd = self.update()
        self.assertEqual(upd.returncode, 0, upd.stderr)
        text = self.plan_path.read_text(encoding="utf-8")
        unplanned = text.split("<!-- BEGIN GENERATED: unplanned -->")[1]
        unplanned = unplanned.split("<!-- END GENERATED: unplanned -->")[0]
        self.assertIn("renamed with no prefix", unplanned)
        milestone_1 = text.split("<!-- BEGIN GENERATED: milestone-1 -->")[1]
        milestone_1 = milestone_1.split("<!-- END GENERATED: milestone-1 -->")[0]
        self.assertNotIn("renamed with no prefix", milestone_1)


class MilestoneAvailability(FakeGhHarness):
    def seed_labels(self) -> None:
        # Labels exist; only milestones are missing, so the tests below
        # pin the milestone-specific block (with everything missing, the
        # label check fires first and masks it).
        (self.state / "labels.json").write_text(json.dumps([
            {"name": n, "color": "cccccc", "description": ""}
            for n in ("milestone-1-core", "milestone-2-polish",
                      "documentation", "good first issue")
        ]))

    def test_issues_only_without_milestones_fails_and_creates_nothing(self) -> None:
        # --issues-only documents that milestones must already exist.
        # On a repo with none, every issue whose plan-declared milestone
        # is missing is skipped and counted as a failure — creating it
        # milestone-less would leave GitHub state permanently incomplete
        # (populate never revisits existing issues).
        self.seed_labels()
        proc = self.populate("--issues-only")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(self.issues_on_fake_github(), [])
        self.assertIn("not available on GitHub", proc.stdout)

    def test_dry_run_predicts_the_same_blocks(self) -> None:
        # The dry run must report — and exit with — exactly what the
        # real run would do, including live-availability blocks.
        self.seed_labels()
        proc = self.populate("--issues-only", "--dry-run")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("! blocked:", proc.stdout)
        self.assertIn("not available on GitHub", proc.stdout)
        self.assertNotIn("+ create issue:", proc.stdout)


class StageFlagConflicts(FakeGhHarness):
    def test_two_only_flags_are_rejected(self) -> None:
        # Any pair of --*-only flags deselects every stage; argparse now
        # rejects the combination outright instead of silently no-oping.
        proc = self.populate("--labels-only", "--milestones-only")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("not allowed with", proc.stderr)


class WriteBackAbort(FakeGhHarness):
    @unittest.skipIf(os.geteuid() == 0, "root ignores file permissions")
    def test_unwritable_plan_aborts_after_first_creation(self) -> None:
        # The crash-safety contract is persist-before-next-mutation, so
        # when persisting fails the run must stop mutating GitHub, not
        # continue with numbers silently lost.
        self.plan_path.chmod(0o444)
        try:
            proc = self.populate()
        finally:
            self.plan_path.chmod(0o644)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(len(self.issues_on_fake_github()), 1)
        self.assertIn("aborting before further mutations", proc.stderr)

        # The rerun is safe: the snapshot matches the orphaned issue by
        # its [MN-k] prefix, and only the remaining three are created.
        rerun = self.populate()
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        titles = [i["title"] for i in self.issues_on_fake_github()]
        self.assertEqual(len(titles), 4)
        self.assertEqual(len(set(titles)), 4, f"duplicates: {titles}")


class LabelReconciliation(FakeGhHarness):
    def test_near_collision_is_reported_not_created(self) -> None:
        (self.state / "labels.json").write_text(json.dumps([
            {"name": "Milestone-1-Core", "color": "ffffff", "description": ""},
        ]))
        proc = self.populate("--dry-run")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("collision", proc.stdout)
        self.assertIn("Milestone-1-Core", proc.stdout)

        live = self.populate()
        self.assertEqual(live.returncode, 1)
        labels = json.loads((self.state / "labels.json").read_text())
        # The plan's `milestone-1-core` was NOT created alongside the
        # existing case-variant; the other two labels were.
        self.assertEqual(
            sorted(l["name"] for l in labels),
            ["Milestone-1-Core", "documentation", "milestone-2-polish"],
        )

    def test_existing_label_never_overwritten(self) -> None:
        (self.state / "labels.json").write_text(json.dumps([
            {"name": "documentation", "color": "000000",
             "description": "Different color"},
        ]))
        proc = self.populate()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        docs = [l for l in json.loads((self.state / "labels.json").read_text())
                if l["name"] == "documentation"]
        self.assertEqual(docs, [{"name": "documentation", "color": "000000",
                                 "description": "Different color"}])
        self.assertIn("not overwritten", proc.stdout)


class UpdateRoundTrip(FakeGhHarness):
    def test_update_rewrites_regions_and_check_tracks_drift(self) -> None:
        self.assertEqual(self.populate().returncode, 0)

        # Freshly populated file is NOT current yet: update canonicalizes
        # the regions (bodies now live on GitHub).
        stale = self.update("--check")
        self.assertEqual(stale.returncode, 1, stale.stderr)

        wrote = self.update()
        self.assertEqual(wrote.returncode, 0, wrote.stderr)
        text = self.plan_path.read_text(encoding="utf-8")
        self.assertIn("## How to use", text)          # manual prose survives
        self.assertIn("### Issue M1-1: Set up the build (#", text)

        current = self.update("--check")
        self.assertEqual(current.returncode, 0, current.stderr)

        # Close an issue on "GitHub"; --check flags drift; update heals it.
        issues = self.issues_on_fake_github()
        issues[0]["state"] = "CLOSED"
        (self.state / "issues.json").write_text(json.dumps(issues))
        drifted = self.update("--check")
        self.assertEqual(drifted.returncode, 1)
        self.assertEqual(self.update().returncode, 0)
        self.assertIn(", closed)", self.plan_path.read_text(encoding="utf-8"))

    def test_update_failure_is_exit_2_not_drift(self) -> None:
        proc = self.run_script(
            "gh_project_update.py", "--check", "--repo", "",
        )
        # An empty --repo falls back to the file header, so break it harder:
        # point at a plan with no header and pass no repo.
        stripped = PLAN.replace("**Repository**:  `octocat/demo`\n", "")
        self.plan_path.write_text(stripped, encoding="utf-8")
        proc = self.run_script("gh_project_update.py", "--check")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("no repository", proc.stderr)


class LineBreakHandling(FakeGhHarness):
    """Issue #6: populate strips authored hard line breaks by default,
    so GitHub soft-wraps prose; --keep-line-breaks opts out.  Either
    way the mode is stated in the output."""

    WRAPPED_BODY = "Body of\nM1-1 wrapped over\nthree lines."
    WRAPPED_DESC = "Build the\ncore, wrapped."

    def write_wrapped_plan(self) -> None:
        self.plan_path.write_text(
            PLAN.replace("Body of M1-1.", self.WRAPPED_BODY)
                .replace("Build the core.", self.WRAPPED_DESC),
            encoding="utf-8",
        )

    def created_m11_body(self) -> str:
        issues = self.issues_on_fake_github()
        return next(i for i in issues
                    if i["title"].startswith("[M1-1]"))["body"]

    def test_default_strips_breaks_from_bodies_and_descriptions(self) -> None:
        self.write_wrapped_plan()
        proc = self.populate()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Line breaks: stripped from prose", proc.stdout)

        self.assertTrue(self.created_m11_body().startswith(
            "Body of M1-1 wrapped over three lines."))
        milestones = json.loads((self.state / "milestones.json").read_text())
        core = next(m for m in milestones if m["title"] == "1. Core")
        self.assertTrue(core["description"].startswith(
            "Build the core, wrapped."))

        # Write-back still lands on the authored (wrapped) file.
        text = self.plan_path.read_text(encoding="utf-8")
        self.assertIn("### Issue M1-1: Set up the build (#", text)
        self.assertIn(self.WRAPPED_BODY, text)  # file itself untouched

    def test_keep_line_breaks_pushes_verbatim(self) -> None:
        self.write_wrapped_plan()
        proc = self.populate("--keep-line-breaks")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Line breaks: preserved as authored", proc.stdout)
        self.assertTrue(self.created_m11_body().startswith(self.WRAPPED_BODY))

    HINT = "fresh plan is ALWAYS stale otherwise"

    def test_next_steps_hint_only_after_fully_successful_runs(self) -> None:
        first = self.populate()
        self.assertIn(self.HINT, first.stdout)
        self.assertIn("`make update`", first.stdout)
        self.assertIn("`make update-check`", first.stdout)
        rerun = self.populate()
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        self.assertNotIn(self.HINT, rerun.stdout)

    def test_no_hint_after_a_partial_failure(self) -> None:
        # Normalizing via update from incomplete GitHub state would drop
        # the not-yet-created issue definitions from the plan file, so
        # a partially failed run must not point users there.
        (self.state / "fail_create_issue_at").write_text("2")
        proc = self.populate()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertGreater(len(self.issues_on_fake_github()), 0)
        self.assertNotIn(self.HINT, proc.stdout)


class SyncBodiesClassification(unittest.TestCase):
    """Pure three-way classification behind --sync-bodies (issue #7)."""

    def setUp(self) -> None:
        sys.path.insert(0, str(SCRIPTS_DIR))
        from gh_project_populate import classify_sync
        self.classify = classify_sync

    def test_identical_is_in_sync(self) -> None:
        self.assertEqual(self.classify("Body.\n", "Body.\n"), "in-sync")

    def test_crlf_and_trailing_whitespace_normalize(self) -> None:
        self.assertEqual(self.classify("Body.\n", "Body.\r\n\r\n"), "in-sync")

    def test_wrapping_only_difference_is_reflow(self) -> None:
        self.assertEqual(
            self.classify("One long joined line here.",
                          "One long\njoined line\nhere."),
            "reflow",
        )

    def test_content_change_is_divergent(self) -> None:
        self.assertEqual(
            self.classify("One long joined line here.",
                          "One long joined line THERE."),
            "divergent",
        )

    def test_leading_indentation_is_meaningful(self) -> None:
        # A four-space-indented first line on GitHub is a code block;
        # eating it made different bodies compare in-sync — and in-sync
        # short-circuits even --force.
        self.assertEqual(self.classify("code\nrest.", "    code\nrest."),
                         "divergent")
        self.assertEqual(self.classify("    code\nrest.", "    code\nrest."),
                         "in-sync")

    def test_github_side_hard_breaks_are_divergent(self) -> None:
        # `a  \nb` renders a <br>; unwrap() would erase it, so reflow
        # must not silently overwrite it (two-space and backslash forms).
        self.assertEqual(self.classify("a b", "a  \nb"), "divergent")
        self.assertEqual(self.classify("a b", "a\\\nb"), "divergent")
        # Identical bodies with hard breaks are still just in-sync.
        self.assertEqual(self.classify("a  \nb", "a  \nb"), "in-sync")

    def test_file_side_hard_breaks_still_reflow(self) -> None:
        # The push carries the file's hard break to GitHub intact —
        # nothing GitHub-side is lost, so no --force is demanded.
        # Both markdown forms: trailing double-space AND backslash (the
        # backslash survives unwrap's join, so it needs explicit
        # defanging in the equivalence comparison).
        self.assertEqual(self.classify("a  \nb", "a\nb"), "reflow")
        self.assertEqual(self.classify("a\\\nb", "a\nb"), "reflow")

    def test_live_runs_classify_against_revalidated_text(self) -> None:
        # The refuse-on-divergence guarantee must cover edits made
        # after the snapshot (e.g. while the confirmation prompt sat
        # open): the executor re-fetches each target immediately before
        # mutating and classifies against THAT.
        import contextlib
        import io
        from gh_project_populate import execute_sync_bodies
        from _utils import Result

        quiet = io.StringIO()
        pushes: list[str] = []
        pair = (
            "issue M1-1 (#1)",
            "Body, reflowed onto one line.",
            "Body,\nreflowed onto\none line.",          # snapshot: reflow-safe
            lambda: Result.ok("Edited on GitHub meanwhile."),  # fresh: divergent
            lambda: (pushes.append("pushed"), Result.ok(None))[1],
        )
        with contextlib.redirect_stdout(quiet):
            problems = execute_sync_bodies(
                None, [pair], [], force=False, dry_run=False, delay=0,
            )
        self.assertIn("divergent", quiet.getvalue())
        self.assertEqual(problems, 1)      # refused on the FRESH text
        self.assertEqual(pushes, [])       # nothing mutated

        # Dry runs preview from the snapshot and never fetch.
        fetches: list[str] = []
        pair = (
            "issue M1-1 (#1)",
            "Body, reflowed onto one line.",
            "Body,\nreflowed onto\none line.",
            lambda: (fetches.append("fetched"), Result.ok(""))[1],
            lambda: Result.ok(None),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            problems = execute_sync_bodies(
                None, [pair], [], force=False, dry_run=True, delay=0,
            )
        self.assertEqual(problems, 0)
        self.assertEqual(fetches, [])


class SyncBodies(FakeGhHarness):
    """--sync-bodies end to end: reflow pushes, divergence refuses
    without --force, dry-run predicts, milestones included."""

    WRAPPED_BODY = "Body of\nM1-1 wrapped over\nthree lines."
    WRAPPED_DESC = "Build the\ncore, wrapped."

    def populate_wrapped(self) -> None:
        self.plan_path.write_text(
            PLAN.replace("Body of M1-1.", self.WRAPPED_BODY)
                .replace("Build the core.", self.WRAPPED_DESC),
            encoding="utf-8",
        )
        proc = self.populate("--keep-line-breaks")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def m11(self) -> dict:
        return next(i for i in self.issues_on_fake_github()
                    if i["title"].startswith("[M1-1]"))

    def core_milestone(self) -> dict:
        milestones = json.loads((self.state / "milestones.json").read_text())
        return next(m for m in milestones if m["title"] == "1. Core")

    def test_reflow_pushes_and_second_run_is_in_sync(self) -> None:
        self.populate_wrapped()
        self.assertIn("\n", self.m11()["body"].split("wrapped")[0] + "wrapped")

        proc = self.populate("--sync-bodies")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("pushed (reflow)", proc.stdout)
        self.assertTrue(self.m11()["body"].startswith(
            "Body of M1-1 wrapped over three lines."))
        self.assertTrue(self.core_milestone()["description"].startswith(
            "Build the core, wrapped."))

        again = self.populate("--sync-bodies")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("0 pushed", again.stdout)
        self.assertNotIn("pushed (", again.stdout)

    def test_divergence_refuses_then_force_wins(self) -> None:
        self.populate_wrapped()
        issues = self.issues_on_fake_github()
        target = next(i for i in issues if i["title"].startswith("[M1-1]"))
        target["body"] = "Rewritten independently on GitHub."
        (self.state / "issues.json").write_text(json.dumps(issues))

        refused = self.populate("--sync-bodies")
        self.assertEqual(refused.returncode, 1, refused.stdout)
        self.assertIn("divergent: issue M1-1", refused.stdout)
        self.assertEqual(self.m11()["body"],
                         "Rewritten independently on GitHub.")

        forced = self.populate("--sync-bodies", "--force")
        self.assertEqual(forced.returncode, 0, forced.stdout + forced.stderr)
        self.assertIn("pushed (forced push)", forced.stdout)
        self.assertTrue(self.m11()["body"].startswith("Body of M1-1"))

    def test_dry_run_predicts_and_mutates_nothing(self) -> None:
        self.populate_wrapped()
        before = json.dumps(self.issues_on_fake_github())
        proc = self.populate("--sync-bodies", "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("would push (reflow)", proc.stdout)
        self.assertEqual(json.dumps(self.issues_on_fake_github()), before)

    def test_sync_never_creates(self) -> None:
        proc = self.populate("--sync-bodies")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.issues_on_fake_github(), [])
        self.assertIn("never creates", proc.stdout)


class OrganicIssues(FakeGhHarness):
    """Issue #3 end to end: organically-filed issues render into the
    `unplanned` region, stay out of populate's way, and cannot inject
    plan structure on reparse."""

    def seed_organic(self) -> None:
        issues = self.issues_on_fake_github()
        issues += [
            {"number": 68, "title": "Harden the flaky timeout",
             "body": "Field-driven fix.",
             "labels": [{"name": "documentation"}],
             "milestone": {"title": "1. Working core"},
             "state": "OPEN", "assignees": []},
            {"number": 70, "title": "Issue M9-9: looks like a plan heading",
             "body": "Adversarial organic title.",
             "labels": [], "milestone": None,
             "state": "OPEN", "assignees": []},
        ]
        (self.state / "issues.json").write_text(json.dumps(issues))

    def unplanned_region(self) -> str:
        text = self.plan_path.read_text(encoding="utf-8")
        inner = text.split("<!-- BEGIN GENERATED: unplanned -->")[1]
        return inner.split("<!-- END GENERATED: unplanned -->")[0]

    def test_rendered_grouped_and_check_stable(self) -> None:
        self.assertEqual(self.populate().returncode, 0)
        self.seed_organic()
        self.assertEqual(self.update().returncode, 0)

        region = self.unplanned_region()
        self.assertIn("#### 1. Working core", region)
        self.assertIn("### Harden the flaky timeout (#68)", region)
        self.assertIn("#### (no milestone)", region)
        self.assertIn("### Issue M9-9: looks like a plan heading (#70)", region)

        # --check-stable across runs (the acceptance criterion).
        self.assertEqual(self.update("--check").returncode, 0)

        # Closing an organic issue is drift, healed by the next update.
        issues = self.issues_on_fake_github()
        next(i for i in issues if i["number"] == 68)["state"] = "CLOSED"
        (self.state / "issues.json").write_text(json.dumps(issues))
        self.assertEqual(self.update("--check").returncode, 1)
        self.assertEqual(self.update().returncode, 0)
        self.assertIn("(#68, closed)", self.unplanned_region())

    def test_mirror_content_never_becomes_plan_structure(self) -> None:
        self.assertEqual(self.populate().returncode, 0)
        self.seed_organic()
        self.assertEqual(self.update().returncode, 0)

        # The updated file — now mirroring an adversarial organic title
        # and a plan-foreign label — still lints clean...
        lint = self.run_script("gh_project_lint.py")
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)

        # ...and a re-populate plans nothing: no phantom M9-9, no
        # duplicate of anything.
        rerun = self.populate("--dry-run")
        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
        self.assertNotIn("+ create issue", rerun.stdout)
        self.assertNotIn("M9-9", rerun.stdout)


class PopulateRefusesBrokenPlans(FakeGhHarness):
    def test_lint_gate(self) -> None:
        self.plan_path.write_text(
            PLAN.replace("### Issue M2-1a: Shine it more",
                         "### Issue M2-1: Shine it more"),
            encoding="utf-8",
        )
        proc = self.populate("--dry-run")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("duplicate issue ID", proc.stderr)
        self.assertEqual(self.calls("octocat/demo/issues"), 0)  # refused before fetching


if __name__ == "__main__":
    unittest.main()
