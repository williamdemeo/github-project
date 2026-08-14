"""
File: scripts/tests/test_file_ops.py

Description:
  Regression tests for _utils.file_ops, pinning the fix for
  ualib/agda-algebras#293 item 3: calculate_file_metadata annotates
  `stage: ProcessingStage` — the name must actually be imported.
  Postponed evaluation (`from __future__ import annotations`) hides the
  missing import at call time, so the test forces annotation resolution
  with typing.get_type_hints, which raises NameError on the unfixed copy.
"""
from __future__ import annotations

import sys
import tempfile
import typing
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _utils import file_ops  # noqa: E402
from _utils.pipeline_types import FileMetadata, ProcessingStage  # noqa: E402


class CalculateFileMetadataAnnotations(unittest.TestCase):
    """#293 item 3: the ProcessingStage annotation must resolve."""

    def test_annotations_resolve(self) -> None:
        hints = typing.get_type_hints(file_ops.calculate_file_metadata)
        self.assertIs(hints["stage"], ProcessingStage)

    def test_metadata_for_existing_file(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".md") as f:
            f.write(b"12345")
            f.flush()
            meta = file_ops.calculate_file_metadata(
                Path(f.name), ProcessingStage.SOURCE
            )
        self.assertIsInstance(meta, FileMetadata)
        self.assertEqual(meta.stage, ProcessingStage.SOURCE)
        self.assertEqual(meta.file_size, 5)

    def test_metadata_for_missing_file_defaults_size_zero(self) -> None:
        meta = file_ops.calculate_file_metadata(
            Path("/nonexistent/never/here.md"), ProcessingStage.SOURCE
        )
        self.assertEqual(meta.file_size, 0)


class WriteTextReturnsOkNone(unittest.TestCase):
    """write_text returns Result[None, _]; combined with the unwrap()
    fix this round-trips without special-casing."""

    def test_write_then_read(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "x.txt"
            w = file_ops.write_text(target, "hello")
            self.assertTrue(w.is_ok)
            self.assertIsNone(w.unwrap())
            r = file_ops.read_text(target)
            self.assertEqual(r.unwrap(), "hello")


if __name__ == "__main__":
    unittest.main()
