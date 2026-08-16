"""
File: scripts/tests/test_makefile_stub.py

Description:
  Tests for the channel-agnostic Makefile stub and the `make init`
  detach step (issue #2): after init, the engine copy is gone, the
  consumer flake is installed, and the stub resolves the engine through
  each remaining channel (PATH CLI, GHPROJECT_DIR checkout) or fails
  with the channel-listing error.

  These run `make` on a throwaway copy of the repository tree — still
  offline, no nix required (the Nix channel itself is exercised by
  `nix flake check` in CI, not here).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# In the Nix sandbox the flake builds from ./scripts alone, so the
# surrounding repository tree (Makefile, templates/) is absent — these
# tests only make sense against a full checkout.
FULL_TREE = (
    (REPO_ROOT / "Makefile").exists()
    and (REPO_ROOT / "templates" / "consumer" / "flake.nix").exists()
)

ENGINE_FILES = (
    "scripts/gh_project_populate.py",
    "scripts/gh_project_update.py",
    "scripts/gh_project_lint.py",
    "scripts/_gh_project_lib.py",
    "scripts/VERSION",
    "scripts/_utils",
    "scripts/tests",
)


@unittest.skipIf(shutil.which("make") is None, "GNU make not available")
@unittest.skipIf(not FULL_TREE, "full repository tree not available")
class MakeInit(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tree = Path(self._tmp.name) / "consumer"
        shutil.copytree(
            REPO_ROOT, self.tree,
            ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "result", ".direnv"),
            symlinks=True,
        )

    def make(self, *args: str, env_extra: dict | None = None,
             cwd: Path | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ, **(env_extra or {})}
        return subprocess.run(
            ["make", *args], cwd=cwd or self.tree,
            env=env, capture_output=True, text=True, timeout=300,
        )

    def run_init(self) -> None:
        proc = self.make("init", "INIT_YES=1")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_init_detaches_the_engine(self) -> None:
        self.run_init()
        for rel in ENGINE_FILES:
            self.assertFalse((self.tree / rel).exists(), rel)
        # The freshness workflow's shell survives; so does the plan.
        self.assertTrue((self.tree / "scripts/ci/project-plan-update.sh").exists())
        self.assertTrue((self.tree / "docs/GITHUB_PROJECT.md").exists())
        # The consumer flake replaced the engine flake; the engine's
        # lock and the template dir are gone.
        flake = (self.tree / "flake.nix").read_text(encoding="utf-8")
        self.assertIn("github-project.url", flake)
        self.assertFalse((self.tree / "flake.lock").exists())
        self.assertFalse((self.tree / "templates").exists())

    def test_init_is_idempotent(self) -> None:
        self.run_init()
        again = self.make("init", "INIT_YES=1")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("Nothing to do", again.stdout)

    def test_post_init_modes(self) -> None:
        self.run_init()

        # No channel available: mode is `missing`, targets fail with the
        # channel-listing error.
        mode = self.make("engine-mode", env_extra={"GHPROJECT_DIR": ""})
        self.assertEqual(mode.stdout.strip(), "missing")
        lint = self.make("lint", env_extra={"GHPROJECT_DIR": ""})
        self.assertNotEqual(lint.returncode, 0)
        self.assertIn("engine was not found", lint.stdout + lint.stderr)

        # Checkout channel: GHPROJECT_DIR pointing at the engine repo.
        lint = self.make("lint", f"GHPROJECT_DIR={REPO_ROOT}")
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
        self.assertIn("0 error(s)", lint.stdout)

        # PATH channel: gh-project-* shims ahead on PATH (stands in for
        # the Nix dev shell / an installed CLI).
        bindir = Path(self._tmp.name) / "bin"
        bindir.mkdir()
        for tool in ("populate", "update", "lint"):
            shim = bindir / f"gh-project-{tool}"
            shim.write_text(
                "#!/bin/sh\n"
                f'exec python3 "{REPO_ROOT}/scripts/gh_project_{tool}.py" "$@"\n',
                encoding="utf-8",
            )
            shim.chmod(0o755)
        env = {"PATH": f"{bindir}:{os.environ['PATH']}", "GHPROJECT_DIR": ""}
        mode = self.make("engine-mode", env_extra=env)
        self.assertEqual(mode.stdout.strip(), "path")
        lint = self.make("lint", env_extra=env)
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)

        # make test degrades to a pointer at the engine repository.
        test = self.make("test")
        self.assertEqual(test.returncode, 0, test.stderr)
        self.assertIn("engine's tests run in its own repository", test.stdout)


@unittest.skipIf(shutil.which("make") is None, "GNU make not available")
@unittest.skipIf(not FULL_TREE, "full repository tree not available")
class PreInitTree(unittest.TestCase):
    def test_engine_repo_resolves_local(self) -> None:
        proc = subprocess.run(
            ["make", "engine-mode"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.stdout.strip(), "local")


if __name__ == "__main__":
    unittest.main()
