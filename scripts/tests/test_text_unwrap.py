"""
File: scripts/tests/test_text_unwrap.py

Description:
  Tests for _utils.text_unwrap: paragraph reflow semantics, structure
  preservation, idempotence, and the acceptance property that matters
  to the engine — a plan file parses IDENTICALLY before and after
  unwrapping (same milestones, labels, issue ids and label sets) and
  still lints clean; only prose reflows.
"""
from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _gh_project_lib import lint_plan, parse_project_plan  # noqa: E402
from _utils.text_unwrap import unwrap  # noqa: E402
from test_gh_project_lib import PLAN  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_PLAN = REPO_ROOT / "docs" / "GITHUB_PROJECT.md"

CASES: tuple[tuple[str, str, str], ...] = (
    (
        "paragraph joins with sentence-aware spacing",
        "One line that\nwraps here.\nNext sentence\ncontinues.\n",
        "One line that wraps here.  Next sentence continues.\n",
    ),
    (
        "blank lines separate paragraphs",
        "First\npara.\n\nSecond\npara.\n",
        "First para.\n\nSecond para.\n",
    ),
    (
        "list items unwrap their continuations but not each other",
        "1.  **Bold.**  Starts\n    and continues (x).\n    More.\n2.  Second\n    item.\n",
        "1.  **Bold.**  Starts and continues (x).  More.\n2.  Second item.\n",
    ),
    (
        "fenced code is verbatim",
        "```mermaid\ngraph TD\n  A --> B\n```\nafter\nfence.\n",
        "```mermaid\ngraph TD\n  A --> B\n```\nafter fence.\n",
    ),
    (
        "indented code after a blank line is verbatim",
        "Prose\nhere.\n\n    cmd one\n    cmd two\n",
        "Prose here.\n\n    cmd one\n    cmd two\n",
    ),
    (
        "structural lines never merge",
        "# Title\n\nText\nhere.\n\n---\n\n| a | b |\n| - | - |\n\n> quoted\n> lines\n",
        "# Title\n\nText here.\n\n---\n\n| a | b |\n| - | - |\n\n> quoted\n> lines\n",
    ),
    (
        "setext underline is not swallowed into its heading text",
        "Title\n---\nbody\ntext.\n",
        "Title\n---\nbody text.\n",
    ),
    (
        "comment and generated-marker lines stay put",
        "<!--\nWrapped\ncomment prose.\n-->\n\n<!-- BEGIN GENERATED: unplanned -->\nmirror\n<!-- END GENERATED: unplanned -->\n",
        "<!--\nWrapped comment prose.\n-->\n\n<!-- BEGIN GENERATED: unplanned -->\nmirror\n<!-- END GENERATED: unplanned -->\n",
    ),
    (
        "adjacent bold-metadata lines are not merged",
        "### Issue M1-1: Set up\n\n**Labels:** `a`, `b`\n**Milestone:** 1. Core\n\nBody\ntext.\n",
        "### Issue M1-1: Set up\n\n**Labels:** `a`, `b`\n**Milestone:** 1. Core\n\nBody text.\n",
    ),
)


class ReflowSemantics(unittest.TestCase):
    def test_cases(self) -> None:
        for name, source, expected in CASES:
            with self.subTest(name):
                self.assertEqual(unwrap(source), expected)

    def test_idempotence(self) -> None:
        for name, source, _ in CASES:
            with self.subTest(name):
                once = unwrap(source)
                self.assertEqual(unwrap(once), once)

    def test_total_on_edge_inputs(self) -> None:
        self.assertEqual(unwrap(""), "")
        self.assertEqual(unwrap("\n"), "\n")
        self.assertEqual(unwrap("no newline at eof"), "no newline at eof")
        self.assertEqual(unwrap("```\nunclosed fence\nstays put\n"),
                         "```\nunclosed fence\nstays put\n")


class PlanFileAcceptance(unittest.TestCase):
    """The property populate relies on: unwrapping changes prose only."""

    def assert_parses_identically(self, text: str) -> None:
        before = parse_project_plan(text)
        unwrapped = unwrap(text)
        after = parse_project_plan(unwrapped)
        self.assertEqual([m.title for m in before.milestones],
                         [m.title for m in after.milestones])
        self.assertEqual([(l.name, l.color) for l in before.labels],
                         [(l.name, l.color) for l in after.labels])
        self.assertEqual([(i.id, i.title, i.labels) for i in before.issues],
                         [(i.id, i.title, i.labels) for i in after.issues])
        self.assertEqual(
            [p.message for p in lint_plan(unwrapped) if p.severity == "error"],
            [],
        )

    def test_fixture_plan(self) -> None:
        self.assert_parses_identically(PLAN)

    @unittest.skipIf(not EXAMPLE_PLAN.exists(),
                     "full repository tree not available")
    def test_shipped_example_plan(self) -> None:
        text = EXAMPLE_PLAN.read_text(encoding="utf-8")
        self.assert_parses_identically(text)
        self.assertIn("```mermaid", unwrap(text))

    def test_wrapped_body_reflows(self) -> None:
        wrapped = PLAN.replace(
            "Body of M2-1.",
            "Body of M2-1\nwraps over lines.\nA second sentence\nwraps too.",
        )
        self.assertNotEqual(wrapped, PLAN)
        self.assert_parses_identically(wrapped)
        issues = {i.id: i for i in parse_project_plan(unwrap(wrapped)).issues}
        self.assertEqual(
            issues["M2-1"].body,
            "Body of M2-1 wraps over lines.  A second sentence wraps too.",
        )


if __name__ == "__main__":
    unittest.main()
