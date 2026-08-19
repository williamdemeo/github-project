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

2. **Detach the engine copy** (one time).  Template creation copies
   this whole repository — including the engine, which has exactly one
   home ([see the channels](#the-engine-getting-and-upgrading-it)
   below).  Keeping a copy would freeze you at creation-day behavior,
   so:

   ```sh
   make init
   ```

   deletes the copied engine, installs a consumer `flake.nix` that
   takes the engine as a pinned input, and prints the next steps
   (`nix flake lock` to pin — or set `GHPROJECT_DIR` to an engine
   checkout if you don't use Nix).  Every `make` target below works
   identically before and after this step.

3. **Author your plan.**  Replace the worked example in
   `docs/GITHUB_PROJECT.md` with your milestones, labels, and issues —
   keep the structure (the example demonstrates every construct exactly
   once).  Set the header line to your repository:

   ```markdown
   **Repository**:  `you/your-project`
   ```

4. **Validate and preview** (lint needs no network; the dry run makes
   read-only API calls):

   ```sh
   make lint
   make populate-dry
   ```

5. **Push the structure to GitHub** (prompts before creating):

   ```sh
   make populate
   ```

   Each created issue's number is written back into the file
   immediately, as a `(#N)` suffix on its heading — commit the result.
   By default populate strips hard line breaks from prose before
   pushing, so GitHub soft-wraps your issue bodies and milestone
   descriptions (`--keep-line-breaks` preserves them verbatim; the run
   output states which mode applied).

   Then run a plain `make update` once and commit it as a
   normalization: update's canonical rendering differs cosmetically
   from authored input (it drops the `**Milestone:**` lines, reorders
   labels to GitHub's order, and trims trailing `---` separators), so
   the first `make update-check` on a freshly populated plan is
   ALWAYS stale — that is normalization pending, not a bug.  After the
   normalization commit, `update-check` is your drift gate.

6. **From then on**: work the issues on GitHub as usual, and run

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

## The engine: getting and upgrading it

Your project owns its **data** — `docs/GITHUB_PROJECT.md`, a thin
Makefile, the workflows.  The **engine** (`gh_project_populate.py`,
`gh_project_update.py`, `gh_project_lint.py` and their library) is
never copied into consumer projects: it has exactly one home, this
repository, and the Makefile finds it through whichever channel is
available:

1. **Nix flake input** (primary).  `make init` installs a `flake.nix`
   taking this repository as an input; your `flake.lock` pins the
   engine version.  `nix develop` puts the `gh-project-*` CLIs on PATH
   (the Makefile finds them there), and the re-exported apps allow
   direct runs:

   ```sh
   nix run .#update -- docs/GITHUB_PROJECT.md
   ```

   Upgrade deliberately:

   ```sh
   nix flake update github-project
   ```

   (If your project already has a flake, merge the input and outputs
   from `templates/consumer/flake.nix` into it instead.)

2. **Checkout** (no Nix).  Clone this repository anywhere and point
   `GHPROJECT_DIR` at it — the engine is stdlib-only Python 3.11+, so a
   plain `python3` runs it:

   ```sh
   make update GHPROJECT_DIR=~/git/github-project
   ```

   Upgrading is `git pull` in that checkout.

3. **Installed CLI** (planned).  A `pyproject.toml` with console entry
   points for `uv tool install` / pipx from a tagged release; the
   Makefile already resolves `gh-project-*` from PATH, so this channel
   needs no consumer-side changes when it lands.

In CI, consumer projects need no engine either: the shipped workflows
fetch an engine checkout automatically when the tree has no local copy —
**pinned**, never floating `main`.  The pin resolves in order: the
`github-project` revision in your `flake.lock` (so Nix consumers have
exactly one pin to manage), the `GHPROJECT_ENGINE_REF` repository
variable, then the default release tag baked into the workflow.

The engine's own `make test` (115 offline tests) and `nix flake check`
run in this repository — `scripts/VERSION` is the engine version your
lock or checkout carries.

## The Nix path

A machine with [Nix](https://nixos.org/) (flakes enabled) needs nothing
else installed — not even Python.  In this repository (or a template
copy): `nix develop` provides python3, gh, and make.  In a detached
consumer project, `nix develop` additionally carries the engine CLIs,
pinned by your lock.

The flake is the primary way the engine is *distributed*, but never a
requirement for *using* it: the setup-python CI jobs and the checkout
channel work without Nix.

## Repository layout

```
docs/GITHUB_PROJECT.md    the roadmap (a worked example until you replace it)
scripts/                  THE ENGINE — lives here and only here; `make init`
                          removes it from projects created off the template
  gh_project_populate.py    file → GitHub
  gh_project_update.py      GitHub → file
  gh_project_lint.py        structural validation, no network
  _gh_project_lib.py        shared parsing, planning, gh client
  _utils/                   functional-Python core (Result, file_ops, ...)
  tests/                    offline test suite (recorded fake `gh`)
  ci/                       workflow shell — survives `make init`
  VERSION                   the engine version (also the flake package version)
templates/consumer/       the flake.nix `make init` installs in consumers
flake.nix                 the engine package + apps (+ dev shell)
.claude/skills/           committed Claude Code skills for the two workflows
.github/workflows/        CI + the opt-in freshness workflow (both self-adapt
                          to trees with no engine copy)
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
- **The engine is referenced, never copied**: consumer projects hold
  data (the plan file, a thin Makefile) and pin the engine by flake
  lock or checkout — a copied engine decays the moment the real one
  improves, which is the failure mode this repository exists to end.
- Provenance: this template distills the gh-project tooling that grew up
  in [agda-algebras](https://github.com/ualib/agda-algebras),
  [williamdemeo.github.io](https://github.com/williamdemeo/williamdemeo.github.io),
  and agda-native-air, and is now its single home — fixes land here and
  reach consumers through their engine pin.

## License

[MIT](LICENSE).
