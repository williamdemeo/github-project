"""
File: scripts/tests/test_command_runner.py

Description:
  Regression tests for _utils.command_runner, pinning the fixes for
  ualib/agda-algebras#293 items 4 and 5:

    4. stream_output=True must not deadlock when the child fills the
       stderr pipe buffer while stdout is still open.  The unfixed code
       drained stdout to completion before touching stderr; a child
       writing more than the OS pipe buffer (~64 KiB) to stderr first
       blocked forever.  The test runs exactly that child under a
       watchdog timeout.
    5. stdout_file must honor `text`.  (The TypeError claimed in #293
       did not reproduce on Python 3.11+ — the child writes to the file
       descriptor directly, bypassing the parent's text-mode wrapper —
       so these tests pin the contract the fix makes explicit: the mode
       of the opened file matches `text`, and the child's output arrives
       intact either way.)
"""
from __future__ import annotations

import logging
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _utils.command_runner import run_command  # noqa: E402


def setUpModule() -> None:
    # The stream-output tests drain 200 KB of child stderr through the
    # logger; without this the test run's output is that flood.
    logging.disable(logging.CRITICAL)


def tearDownModule() -> None:
    logging.disable(logging.NOTSET)

# Comfortably beyond any OS pipe buffer (Linux default: 64 KiB).
STDERR_FLOOD = 200_000

# The deadlock child: floods stderr BEFORE writing anything to stdout,
# so a reader that starts with stdout blocks while the child blocks on
# the full stderr pipe.
DEADLOCK_CHILD = (
    "import sys; "
    f"sys.stderr.write('x' * {STDERR_FLOOD}); sys.stderr.flush(); "
    "sys.stdout.write('done'); sys.stdout.flush()"
)


class StreamOutputDoesNotDeadlock(unittest.TestCase):
    """#293 item 4: concurrent draining of stdout and stderr."""

    def _run_with_watchdog(self, argv: list[str], timeout: float = 30.0):
        holder: dict = {}

        def call() -> None:
            holder["result"] = run_command(argv, stream_output=True)

        t = threading.Thread(target=call, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            self.fail(
                f"run_command(stream_output=True) did not finish within "
                f"{timeout}s — the stdout-before-stderr deadlock is back"
            )
        return holder["result"]

    def test_stderr_flood_completes(self) -> None:
        result = self._run_with_watchdog(
            [sys.executable, "-c", DEADLOCK_CHILD]
        )
        self.assertTrue(result.is_ok)
        self.assertEqual(result.unwrap().returncode, 0)

    def test_nonzero_exit_is_err(self) -> None:
        result = self._run_with_watchdog(
            [sys.executable, "-c", "import sys; sys.exit(3)"]
        )
        self.assertTrue(result.is_err)


class StdoutFileHonorsTextFlag(unittest.TestCase):
    """#293 item 5: the file mode matches `text` in both directions."""

    def test_text_false_writes_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.bin"
            result = run_command(
                [sys.executable, "-c", "print('abc')"],
                stdout_file=out,
                text=False,
            )
            self.assertTrue(result.is_ok)
            self.assertEqual(out.read_bytes(), b"abc\n")

    def test_text_true_writes_text(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.txt"
            result = run_command(
                [sys.executable, "-c", "print('abc')"],
                stdout_file=out,
                text=True,
            )
            self.assertTrue(result.is_ok)
            self.assertEqual(out.read_text(encoding="utf-8"), "abc\n")

    def test_missing_command_is_err(self) -> None:
        result = run_command(["definitely-not-a-command-xyzzy"])
        self.assertTrue(result.is_err)


if __name__ == "__main__":
    unittest.main()
