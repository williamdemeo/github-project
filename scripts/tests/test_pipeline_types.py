"""
File: scripts/tests/test_pipeline_types.py

Description:
  Regression tests for _utils.pipeline_types, pinning the fixes for
  ualib/agda-algebras#293 items 1 and 2:

    1. Result.unwrap() must accept Ok(None) — success extraction keys
       solely off _is_ok, never off the contained value.  (write_text
       returns Result[None, _], so Ok(None) is routine, not exotic.)
    2. Result.map() must not catch exceptions raised by the mapped
       function and repackage them as wrong-typed errors; a raising f
       is a bug in the caller and propagates.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _utils.pipeline_types import (  # noqa: E402
    ErrorType,
    FileMetadata,
    PipelineError,
    ProcessedFile,
    ProcessingStage,
    Result,
    collect_errors,
    sequence_results,
)


class UnwrapAcceptsOkNone(unittest.TestCase):
    """#293 item 1: Ok(None) is a legal, common success value."""

    def test_unwrap_ok_none_returns_none(self) -> None:
        self.assertIsNone(Result.ok(None).unwrap())

    def test_unwrap_or_ok_none_returns_none_not_default(self) -> None:
        # Same defect class as unwrap(): the eliminator must key off
        # _is_ok, not off the contained value.
        self.assertIsNone(Result.ok(None).unwrap_or("default"))

    def test_unwrap_err_still_raises(self) -> None:
        err = PipelineError(ErrorType.VALIDATION_ERROR, "boom")
        with self.assertRaises(ValueError):
            Result.err(err).unwrap()

    def test_unwrap_or_err_returns_default(self) -> None:
        err = PipelineError(ErrorType.VALIDATION_ERROR, "boom")
        self.assertEqual(Result.err(err).unwrap_or("default"), "default")

    def test_ok_none_is_ok(self) -> None:
        self.assertTrue(Result.ok(None).is_ok)
        self.assertFalse(Result.ok(None).is_err)


class MapDoesNotSwallowExceptions(unittest.TestCase):
    """#293 item 2: map() previously synthesized Result.err(Exception),
    violating the declared Result[_, PipelineError] error type."""

    def test_exception_in_mapped_function_propagates(self) -> None:
        with self.assertRaises(ZeroDivisionError):
            Result.ok(1).map(lambda x: x / 0)

    def test_map_ok(self) -> None:
        self.assertEqual(Result.ok(2).map(lambda x: x * 3).unwrap(), 6)

    def test_map_err_preserves_error(self) -> None:
        err = PipelineError(ErrorType.VALIDATION_ERROR, "boom")
        r = Result.err(err).map(lambda x: x * 3)
        self.assertTrue(r.is_err)
        self.assertIs(r.unwrap_err(), err)

    def test_map_ok_none_maps_the_none(self) -> None:
        # map over Ok(None) hands None to f (it is the success value).
        self.assertEqual(Result.ok(None).map(lambda v: v is None).unwrap(), True)

    def test_and_then_chains(self) -> None:
        r = Result.ok(2).and_then(lambda x: Result.ok(x + 1))
        self.assertEqual(r.unwrap(), 3)


class IsAgdaFile(unittest.TestCase):
    """Path.suffix sees only `.md` for `Module.lagda.md`; the check must
    match the filename so literate Markdown Agda sources count."""

    def _pf(self, name: str) -> ProcessedFile:
        meta = FileMetadata(
            relative_path=Path(name), stage=ProcessingStage.SOURCE,
            processing_time=0.0, file_size=0,
        )
        return ProcessedFile(
            source_path=Path("src") / name, current_path=Path(name),
            metadata=meta,
        )

    def test_all_supported_forms(self) -> None:
        for name in ("Module.agda", "Module.lagda", "Module.lagda.md"):
            self.assertTrue(self._pf(name).is_agda_file, name)

    def test_non_agda_files(self) -> None:
        for name in ("Module.md", "Module.py", "Module.lagda.rst"):
            self.assertFalse(self._pf(name).is_agda_file, name)


class TraversalsHandleOkNone(unittest.TestCase):
    """The list traversals must not misclassify Ok(None) either."""

    def test_sequence_results_with_ok_none(self) -> None:
        r = sequence_results([Result.ok(None), Result.ok(1)])
        self.assertTrue(r.is_ok)
        self.assertEqual(r.unwrap(), [None, 1])

    def test_collect_errors_with_ok_none(self) -> None:
        err = PipelineError(ErrorType.VALIDATION_ERROR, "boom")
        oks, errs = collect_errors([Result.ok(None), Result.err(err)])
        self.assertEqual(oks, [None])
        self.assertEqual(errs, [err])


if __name__ == "__main__":
    unittest.main()
