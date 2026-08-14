"""
File: scripts/_utils/command_runner.py

Description:
  Functional command execution utilities.

Provenance:
  This repository is the upstream of this module.  Originally adapted
  from formalverification/agda-native-air (SHA 664b919); identical
  across agda-native-air, agda-algebras, and williamdemeo.github.io as
  of 2026-08-13.  This copy additionally fixes ualib/agda-algebras#293
  items 4 (stream_output deadlock: both pipes are now drained
  concurrently) and 5 (stdout_file honors the text flag).  Downstream
  projects re-vendor from here.
"""
from __future__ import annotations
import subprocess
import logging
import threading
from pathlib import Path
from typing import List, Optional

from .pipeline_types import Result, PipelineError, ErrorType

def run_command(
    command: List[str],
    cwd: Optional[Path] = None,
    capture_output: bool = False,
    text: bool = False,
    stdout_file: Optional[Path] = None,
    stream_output: bool = False  # --- NEW: Parameter to enable live streaming
) -> Result[subprocess.CompletedProcess, PipelineError]:
    """
    Runs a shell command and returns a Result object.
    Can either capture output or stream it live to the logger.
    """
    command_str = ' '.join(map(str, command))
    logging.debug(f"Running: {command_str}")

    try:
        # --- Live streaming output ---
        if stream_output:
            # Use Popen for live output streaming
            process = subprocess.Popen(
                [str(arg) for arg in command],
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8'
            )

            # Drain both pipes concurrently.  Reading stdout to
            # completion before touching stderr deadlocks as soon as the
            # child fills the OS stderr pipe buffer (~64 KiB) while its
            # stdout is still open: the child blocks on write, this
            # process on read, and neither can proceed.
            def _drain(pipe, emit) -> None:
                for line in iter(pipe.readline, ''):
                    emit(line.strip())

            readers = [
                threading.Thread(
                    target=_drain,
                    args=(process.stdout, lambda s: logging.info(f"  > {s}")),
                ),
                threading.Thread(
                    target=_drain,
                    args=(process.stderr, lambda s: logging.warning(f"  ! {s}")),
                ),
            ]
            for reader in readers:
                reader.start()
            for reader in readers:
                reader.join()
            process.stdout.close()
            process.stderr.close()

            # Wait for the process to complete and get the return code
            process.wait()
            if process.returncode != 0:
                 return Result.err(PipelineError(
                    error_type=ErrorType.COMMAND_FAILED,
                    message=f"Streamed command failed with exit code {process.returncode}",
                    context={"command": command_str, "return_code": process.returncode}
                ))
            # Return a CompletedProcess-like object on success
            return Result.ok(subprocess.CompletedProcess(
                args=command, returncode=0, stdout="", stderr=""
            ))

        # --- Original logic for capturing output ---
        else:
            stdout_target = subprocess.PIPE if capture_output or not stdout_file else None
            if stdout_file:
                stdout_file.parent.mkdir(parents=True, exist_ok=True)
                # The file's mode must match `text`; the child writes to
                # the descriptor directly, so a text-mode wrapper on a
                # text=False run misdescribes what lands in the file.
                stdout_target = open(
                    stdout_file,
                    "w" if text else "wb",
                    encoding="utf-8" if text else None,
                )

            process = subprocess.run(
                [str(arg) for arg in command],
                cwd=cwd, stdout=stdout_target, stderr=subprocess.PIPE,
                text=text, check=False, encoding='utf-8' if text else None
            )

            if process.stderr:
                logging.debug(f"Stderr for '{command_str}':\n{process.stderr}")
            if process.returncode != 0:
                return Result.err(PipelineError(
                    error_type=ErrorType.COMMAND_FAILED,
                    message=f"Command failed with exit code {process.returncode}",
                    context={
                        "command": command_str,
                        "return_code": process.returncode,
                        "stderr": process.stderr.strip() if process.stderr else "N/A",
                        "cwd": str(cwd) if cwd else "N/A"
                    }
                ))
            return Result.ok(process)

    except FileNotFoundError as e:
        return Result.err(PipelineError(
            error_type=ErrorType.COMMAND_FAILED,
            message=f"Command not found: {command[0]}",
            cause=e
        ))
    except Exception as e:
        return Result.err(PipelineError(
            error_type=ErrorType.COMMAND_FAILED,
            message=f"An unexpected error occurred running command: {e}",
            cause=e
        ))
    finally:
        if not stream_output and stdout_file and 'stdout_target' in locals() and stdout_target:
            if not isinstance(stdout_target, int): # Not a PIPE
                 stdout_target.close()
