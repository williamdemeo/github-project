<!-- File: docs/HowToRun.md -->

# Running the engine — command reference

Man-page-style reference for the three tools that operate on
`docs/GITHUB_PROJECT.md`.  The quickstart lives in the
[README](../README.md); this page is the complete option-by-option
account.

Every tool can be invoked through any engine
[channel](../README.md#the-engine-getting-and-upgrading-it); the forms
are interchangeable:

```sh
make populate                                   # Makefile target (primary UX)
gh-project-populate docs/GITHUB_PROJECT.md      # installed CLI (Nix dev shell)
nix run .#populate -- docs/GITHUB_PROJECT.md    # flake app
python3 scripts/gh_project_populate.py docs/GITHUB_PROJECT.md   # plain python
```

The Makefile targets pass `PLAN` (default `docs/GITHUB_PROJECT.md`),
`REPO`, and `NO_ENV_PREFIX` through; any other option is a direct-
invocation affair.

---

## gh-project-populate

### SYNOPSIS

    gh-project-populate MARKDOWN [--repo OWNER/NAME] [--dry-run]
                        [--milestones-only | --labels-only | --issues-only |
                         --sync-bodies] [--force] [--skip-labels]
                        [--keep-line-breaks] [--start-from ID]
                        [--delay SECONDS] [--yes]
                        [--env-prefix | --no-env-prefix]

### DESCRIPTION

The file → GitHub direction.  Creates the plan's labels, milestones,
and issues on GitHub, writing each new issue's number back into the
plan file (as a `(#N)` heading suffix) immediately after creation, so
an interrupted run is always rerun-safe.  Idempotent: one snapshot of
live GitHub state is fetched per run and everything that already
exists is skipped — labels by exact name, milestones by exact title,
issues by their `[MN-k]` title prefix or recorded `(#N)` number.

By default, hard line breaks in prose are stripped before pushing
(see `--keep-line-breaks`), so GitHub soft-wraps issue bodies and
milestone descriptions.

`--sync-bodies` switches the tool into its one deliberate
GitHub-mutating maintenance mode: pushing bodies of *already existing*
items (see below).

### OPTIONS

- `MARKDOWN`
  Path to the project plan markdown file (positional, required).

- `--repo OWNER/NAME`
  Target repository.  Overrides the plan file's
  `**Repository**:` header; one of the two must be present.

- `--dry-run`
  Print exactly the plan a real run would execute — including
  live-availability blocks and, under `--sync-bodies`, the per-item
  sync classification — and mutate nothing.  Reads still happen, so
  the preview reflects real GitHub state.  The exit code mirrors what
  the real run would return.

- `--milestones-only` / `--labels-only` / `--issues-only`
  Run a single creation stage.  Mutually exclusive with each other and
  with `--sync-bodies`.  `--issues-only` requires the plan's milestones
  AND every label its issues reference to already exist on GitHub —
  this mode creates neither: an issue whose declared milestone or any
  referenced label is unavailable is not created and counts as a
  failure, rather than being created incomplete (populate never
  revisits existing issues, so the incompleteness would be permanent).

- `--sync-bodies`
  Create nothing; push existing issues' bodies and milestone
  descriptions from the plan file to GitHub — the per-issue inverse of
  `update`.  Each target is classified, live runs re-validating against
  a fresh read immediately before mutating:

  | verdict | meaning | action |
  | --- | --- | --- |
  | `in-sync` | identical after CRLF/trailing-whitespace normalization; or the file holds update's rendering artifacts for this body (the escaped form of marker-like text, or the empty-body placeholder) | skip |
  | `reflow` | identical after unwrapping both sides — only line wrapping differs, so pushing loses neither words nor GitHub-side formatting | push |
  | `divergent` | content differs, or GitHub's body carries hard-break or region-marker text a push would destroy; no base version exists to tell which side moved | refuse (exit 1) unless `--force` |

  Refusals are per item: one divergent body does not block the
  reflow-safe rest.

- `--force`
  With `--sync-bodies` only: push divergent items too — last writer
  wins, including any escape artifacts the file may carry.  Rejected
  in every other mode.

- `--skip-labels`
  Skip the label-creation stage in a full run.

- `--keep-line-breaks`
  Push bodies and descriptions exactly as authored.  By default the
  input is unwrapped in memory before parsing (the plan file on disk
  is never rewritten by this), so authored ~72-column prose reaches
  GitHub as single-line paragraphs that the issue page soft-wraps.
  The run output states which mode applied.

- `--start-from ID`
  Consider only issues with ID ≥ `ID` (e.g. `M1-3`), by
  (milestone, ordinal, suffix) order.  Composes with `--issues-only`
  for resuming an interrupted bootstrap, and with `--sync-bodies`.

- `--delay SECONDS`
  Pause between mutating API calls (default: 1.0) to stay clear of
  rate limits.  Reads are not delayed.

- `--yes`
  Skip the interactive confirmation (for scripts and CI).

- `--env-prefix` / `--no-env-prefix`
  By default every `gh` call is prefixed with
  `env -u GH_TOKEN -u GITHUB_TOKEN`, preventing those variables from
  shadowing a keychain-stored token.  Pass `--no-env-prefix` wherever
  authentication comes *through* those variables — GitHub Actions
  included: stripping them there leaves `gh` with no credentials.

### EXIT STATUS

    0  everything requested was created / synced, already existed, or
       was deliberately filtered (--start-from)
    1  some items failed or were refused: creation errors, issues
       blocked by an unavailable label or milestone, label
       near-collisions, divergent bodies under --sync-bodies
    2  the run could not proceed (unreadable file, lint errors, no
       repository, snapshot failure, invalid flag combination)

### EXAMPLES

```sh
# Preview a fresh bootstrap:
gh-project-populate docs/GITHUB_PROJECT.md --dry-run

# Bootstrap, keeping authored line breaks:
gh-project-populate docs/GITHUB_PROJECT.md --keep-line-breaks

# Resume creation from M2-1 after an interruption:
gh-project-populate docs/GITHUB_PROJECT.md --issues-only --start-from M2-1

# Preview what a body sync would do, then do it:
gh-project-populate docs/GITHUB_PROJECT.md --sync-bodies --dry-run
gh-project-populate docs/GITHUB_PROJECT.md --sync-bodies

# Make the file win over independently edited GitHub bodies:
gh-project-populate docs/GITHUB_PROJECT.md --sync-bodies --force
```

---

## gh-project-update

### SYNOPSIS

    gh-project-update MARKDOWN [--repo OWNER/NAME] [--check]
                      [--no-env-prefix]

### DESCRIPTION

The GitHub → file direction.  Rewrites only the regions between
`<!-- BEGIN GENERATED: ... -->` / `<!-- END GENERATED: ... -->`
markers from live GitHub state — `milestone-N` regions from issues
carrying the `milestone-N-*` label, the `unplanned` region from
organically-filed issues (no `[MN-k]` prefix) grouped by GitHub
milestone.  Hand-written prose outside the markers is preserved
byte-for-byte.

Note: the first run after a fresh populate is a *normalization* (its
rendering drops `**Milestone:**` lines, reorders labels to GitHub's
order, trims trailing `---`); commit it once, then treat `--check`
failures as drift.

### OPTIONS

- `MARKDOWN`
  Path to the plan file (positional, required).

- `--repo OWNER/NAME`
  Target repository; overrides the `**Repository**:` header.

- `--check`
  Verify the file already matches the rendered output; write nothing.

- `--no-env-prefix`
  As in populate.

### EXIT STATUS

Follows diff(1), so callers can tell "stale" from "the check never ran":

    0  the file is current (or, without --check, was written)
    1  --check only: the file differs from live GitHub state
    2  the run failed — authentication, API error, unreadable file,
       bad markers

(`make update-check` cannot relay code 1 — GNU make maps any recipe
failure to its own exit 2; call the script directly when the
distinction matters.)

---

## gh-project-lint

### SYNOPSIS

    gh-project-lint MARKDOWN

### DESCRIPTION

Structural validation of the plan file — no network, no `gh`.  Checks
marker balance and uniqueness, heading well-formedness, the
`## Labels` section grammar (including case/whitespace collisions),
milestone-reference consistency, and issue-ID uniqueness.  Populate
runs the same checks and refuses to mutate anything when they fail.
Cheap enough for every PR: this is the repository's no-network drift
gate.

### EXIT STATUS

    0  no errors (warnings may have been printed)
    1  at least one error
    2  the file could not be read

---

## Stripping hard line breaks from any markdown file

The unwrap logic populate uses lives in the engine's `_utils` package
(`scripts/_utils/text_unwrap.py`) as a pure function —
structure-aware (headings, tables, fenced/indented code, blockquotes,
region markers, and the plan grammar's `**Labels:**`-style metadata
all survive byte-for-byte; list STRUCTURE is preserved — markers, one
item per line, empty items untouched — while wrapped prose INSIDE an
item is reflowed onto its marker line, exactly like any paragraph),
sentence-spacing-preserving, and idempotent.  To use it directly on a file, from an engine checkout:

```sh
python3 - docs/SOMEFILE.md <<'EOF'
import sys
sys.path.insert(0, "scripts")
from pathlib import Path
from _utils.text_unwrap import unwrap

path = Path(sys.argv[1])
path.write_text(unwrap(path.read_text(encoding="utf-8")), encoding="utf-8")
EOF
```

Or as a tiny reusable script on your PATH (adjust the checkout path):

```python
#!/usr/bin/env python3
"""md-unwrap FILE [FILE ...] — remove hard line breaks from markdown prose."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "git/github-project/scripts"))
from _utils.text_unwrap import unwrap

for name in sys.argv[1:]:
    path = Path(name)
    text = path.read_text(encoding="utf-8")
    result = unwrap(text)
    path.write_text(result, encoding="utf-8")
    print(("unwrapped: " if result != text else "unchanged: ") + name)
```

For plan files specifically, prefer letting populate do it (the
default), and run `gh-project-lint` afterwards if you unwrap by hand —
the transform is verified to leave plan parsing identical, and lint is
the cheap proof on your own file.
