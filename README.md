<!-- File: README.md -->

# github-project

A template for creating and working on GitHub projects, especially
(though not exclusively) useful for AI-assisted projects.

One markdown file — [`docs/GITHUB_PROJECT.md`](docs/GITHUB_PROJECT.md) —
holds your roadmap: milestones, labels, and issues, written by hand (or
by your AI assistant) before anything exists on GitHub.  Two commands
keep that file and GitHub in sync, in opposite directions:

| Command         | Direction     | What moves                                                        |
| --------------- | ------------- | ----------------------------------------------------------------- |
| `make populate` | file → GitHub | creates labels, milestones, issues; writes issue numbers back into the file |
| `make update`   | GitHub → file | rewrites the *generated regions* with live issue state; hand-written prose is never touched |

("Update" names the user's intent — bring `docs/GITHUB_PROJECT.md` up
to date — not a push to GitHub.  The push direction is `populate`.)

## The contract

One source of truth per concern:

| Concern                                                                  | Source of truth          | Who edits it              |
| ------------------------------------------------------------------------ | ------------------------ | ------------------------- |
| **Structure**: milestones, labels, issue definitions, prose, dependency graphs | `docs/GITHUB_PROJECT.md` | humans (and Claude)       |
| **State**: issue numbers, open/closed, assignees                          | GitHub                   | GitHub UI / normal workflow |

`populate` pushes structure to GitHub once, at bootstrap; it is
idempotent, so re-running it never duplicates anything.  `update` pulls
state back into the file for as long as the project lives, rewriting
only the regions between `<!-- BEGIN GENERATED: ... -->` /
`<!-- END GENERATED: ... -->` markers.  After bootstrap, issue bodies
live on GitHub like any other issue; the generated regions mirror them.

## Quickstart

Requirements: Python 3.11+, GNU make, and the
[GitHub CLI](https://cli.github.com/) authenticated with write access to
your repository (`gh auth status`).  No third-party Python packages.
(Alternatively, [use Nix](#the-nix-path) and skip installing any of
that.)

1. **Create your repository from this template** (the "Use this
   template" button on GitHub, or):

   ```sh
   gh repo create you/your-project --template williamdemeo/github-project --private --clone
   cd your-project
   ```

2. **Author your plan.**  Replace the worked example in
   `docs/GITHUB_PROJECT.md` with your milestones, labels, and issues —
   keep the structure (the example demonstrates every construct exactly
   once).  Set the header line to your repository:

   ```markdown
   **Repository**:  `you/your-project`
   ```

3. **Validate and preview** (lint needs no network; the dry run makes
   read-only API calls):

   ```sh
   make lint
   make populate-dry
   ```

4. **Push the structure to GitHub** (prompts before creating):

   ```sh
   make populate
   ```

   Each created issue's number is written back into the file
   immediately, as a `(#N)` suffix on its heading — commit the result.

5. **From then on**: work the issues on GitHub as usual, and run

   ```sh
   make update
   ```

   whenever you want the file to reflect reality (state flips like
   `(#7)` → `(#7, closed)`, assignees, edited bodies).  Commit the
   result.  `make update-check` tells you whether that is needed: it
   succeeds when the file is current and fails otherwise, with output
   saying whether the file drifted or the check itself could not run.
   (When an exit code must distinguish those two — as the shipped
   freshness workflow does — call the script directly:
   `python3 scripts/gh_project_update.py docs/GITHUB_PROJECT.md --check`
   follows diff(1): 0 current, 1 drifted, 2 failed.  `make` collapses
   any recipe failure to its own exit 2.)

`make help` lists every target.

## Staying fresh automatically (opt-in)

The repository ships a scheduled workflow
(`.github/workflows/project-plan-update.yml`, Mondays + manual
dispatch) that by default only **reports** drift in the run's step
summary — it never commits.  To let it act, set the repository variable
`PROJECT_PLAN_AUTO_UPDATE` (Settings → Secrets and variables → Actions
→ Variables) to:

- `pr` — run `make update` and open (or refresh) a pull request.  Also
  enable "Allow GitHub Actions to create and approve pull requests" in
  Settings → Actions.
- `commit` — run `make update` and push directly to the branch.

Delete the workflow file if you want no scheduled runs at all.

## Upgrading a project created from this template

Template consumers fork at creation time and never see later
improvements — except that the tooling is deliberately confined to two
self-contained paths (`scripts/`, plus the workflow shell in
`scripts/ci/`), so re-vendoring is one command.  From your project's
root:

```sh
V=$(curl -fsSL https://raw.githubusercontent.com/williamdemeo/github-project/main/scripts/VERSION) \
  && curl -fsSL https://github.com/williamdemeo/github-project/archive/refs/tags/v$V.tar.gz \
  | tar -xz --strip-components=1 "github-project-$V/scripts" "github-project-$V/Makefile"
```

Then run `make test` and review `git diff` before committing.  Your
`docs/GITHUB_PROJECT.md`, workflows, and everything else are untouched;
`scripts/VERSION` records which release you now carry.

## The Nix path

A machine with [Nix](https://nixos.org/) (flakes enabled) needs nothing
else installed — not even Python:

```sh
nix develop        # provides python3, gh, gnumake
make lint
```

The flake is a convenience, never a requirement: this repository's own
CI uses plain `setup-python`, and everything documented above works
without Nix.

## Repository layout

```
docs/GITHUB_PROJECT.md    the roadmap (a worked example until you replace it)
scripts/                  self-contained tooling (re-vendorable as a unit)
  gh_project_populate.py    file → GitHub
  gh_project_update.py      GitHub → file
  gh_project_lint.py        structural validation, no network
  _gh_project_lib.py        shared parsing, planning, gh client
  _utils/                   vendored functional-Python core (Result, file_ops, ...)
  tests/                    offline test suite (recorded fake `gh`)
  VERSION                   which release of the tooling this tree carries
.claude/skills/           committed Claude Code skills for the two workflows
.github/workflows/        CI + the opt-in freshness workflow
```

## Design notes

- **Idempotent by snapshot**: populate fetches live state once per run
  and plans against it — rerunning after any interruption is always
  safe, and `--dry-run` prints exactly the plan a real run executes.
- **Crash-safe write-back**: issue numbers land in the file after each
  creation, not at the end.
- **Label reconciliation, not imposition**: existing labels are never
  overwritten, and near-collisions (`era: conway` vs `era:conway`) are
  reported instead of silently creating a parallel scheme.
- **Staged and rate-limit-aware**: `--labels-only`, `--milestones-only`,
  `--issues-only`, `--start-from M2-3`, `--delay`.
- Provenance: this template distills the gh-project tooling that grew up
  in [agda-algebras](https://github.com/ualib/agda-algebras),
  [williamdemeo.github.io](https://github.com/williamdemeo/williamdemeo.github.io),
  and agda-native-air, and is now its upstream — fixes land here and
  downstream projects re-vendor.

## License

[MIT](LICENSE).
