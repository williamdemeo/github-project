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

        # Update still works: the prefix-less issue simply leaves the
        # region (it has no stable identifier to render under).
        upd = self.update()
        self.assertEqual(upd.returncode, 0, upd.stderr)
        text = self.plan_path.read_text(encoding="utf-8")
        self.assertNotIn("renamed with no prefix", text)


class MilestoneAvailability(FakeGhHarness):
    def test_issues_only_without_milestones_fails_and_creates_nothing(self) -> None:
        # --issues-only documents that milestones must already exist.
        # On a repo with none, every issue whose plan-declared milestone
        # is missing is skipped and counted as a failure — creating it
        # milestone-less would leave GitHub state permanently incomplete
        # (populate never revisits existing issues).
        proc = self.populate("--issues-only")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(self.issues_on_fake_github(), [])
        self.assertIn("not available on GitHub", proc.stdout)


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
