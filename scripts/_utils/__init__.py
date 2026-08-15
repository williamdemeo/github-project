"""
File: scripts/_utils/__init__.py

Description:
  Utility modules for functional programming and pipeline operations.

  This package provides immutable data structures, functional error
  handling, and pure transformations.

Provenance:
  This repository is the upstream of this package.  Originally adapted
  from formalverification/agda-native-air (SHA 664b919); identical
  across agda-native-air, agda-algebras, and williamdemeo.github.io as
  of 2026-08-13, plus the ualib/agda-algebras#293 fixes.  Downstream
  projects re-vendor from here — see scripts/VERSION and the README's
  upgrade section.
"""

from .pipeline_types import (
    # Functional error handling
    Result,
    PipelineError,
    ErrorType,

    # Pipeline state management
    PipelineState,
    PipelineStatistics,
    ProcessedFile,
    FileMetadata,
    ProcessingStage,

    # Command execution
    CommandResult,

    # Function types
    ProcessingFunction,
    FileTransformer,
    PipelineStage,

    # Utility functions
    sequence_results,
    collect_errors,
)

__all__ = [
    # Error handling
    "Result",
    "PipelineError",
    "ErrorType",

    # State management
    "PipelineState",
    "PipelineStatistics",
    "ProcessedFile",
    "FileMetadata",
    "ProcessingStage",

    # Command execution
    "CommandResult",

    # Function types
    "ProcessingFunction",
    "FileTransformer",
    "PipelineStage",

    # Utilities
    "sequence_results",
    "collect_errors",
]
