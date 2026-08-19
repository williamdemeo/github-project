---
name: project-setup
description: Set up a new GitHub project from docs/GITHUB_PROJECT.md — author the plan (milestones, labels, issues), validate it, and push it to GitHub with make populate. Use when starting a project from this template, when the user asks to "create the roadmap", "set up the project board", "populate GitHub", or when docs/GITHUB_PROJECT.md still contains the shipped example.
---

# Set up a new project from GITHUB_PROJECT.md

`docs/GITHUB_PROJECT.md` is the single source of truth for project
STRUCTURE (milestones, labels, issue definitions, prose, dependency
graphs).  GitHub owns STATE (issue numbers, open/closed, assignees).
This skill covers the one-time bootstrap: author the file, then push it
to GitHub.

## Procedure

0. **Detach the engine copy** (fresh template copies only): `make init`
   deletes the copied engine — its only home is
   williamdemeo/github-project — and installs a consumer flake taking
   it as a pinned input.  Then `nix flake lock` (or set `GHPROJECT_DIR`
   to an engine checkout).  Skip if `scripts/gh_project_populate.py` is
   already absent.  All make targets below work the same before and
   after.
1. **Author the plan** by replacing the worked example in
   `docs/GITHUB_PROJECT.md`, keeping every structural convention:
   - `**Repository**:  \`owner/name\`` header line (this is where the
     scripts learn the target repo; no `--repo` flag needed).
   - `## Labels` section, one entry per line:
     `` - `name` (RRGGBB) — Description ``.  Include one
     `milestone-N-<slug>` label per milestone — these are load-bearing
     (update uses them to place issues into regions).
   - `## Milestones` section with `### Milestone N — Title` blocks, each
     carrying `**Description:**` and `**Exit criterion:**`.
   - Per-milestone issue sections wrapped in markers:
     `<!-- BEGIN GENERATED: milestone-N -->` ... `<!-- END GENERATED: milestone-N -->`,
     containing `### Issue MN-k: Title` headings with `**Labels:**`
     and `**Milestone:**` metadata lines, separated by `---`.
   - Optional but valuable: a hand-authored mermaid dependency graph.
2. **Validate** (no network): `make lint` — fix every error it reports.
   Warnings are advisories.
3. **Preview**: `make populate-dry` — read the plan it prints.  `+`
   lines will be created; `-` lines already exist; `!` lines are label
   collisions you must resolve first (rename in the plan or on GitHub —
   the tool never creates a near-duplicate label scheme).
4. **Push**: `make populate` (append `--` nothing; it prompts before
   creating; use `python3 scripts/gh_project_populate.py docs/GITHUB_PROJECT.md --yes`
   for non-interactive runs).  Issue numbers are written back into the
   headings as `(#N)` suffixes as each issue is created.
5. **Canonicalize**: run a plain `make update` once and commit it.
   This is a required normalization, not optional polish: update's
   canonical rendering drops `**Milestone:**` lines, reorders labels to
   GitHub's order, and trims trailing `---`, so the first
   `update --check` on a freshly populated plan is ALWAYS stale until
   this run lands.  populate prints this sequence as its next-steps
   hint.  (populate also strips hard line breaks from prose by default
   so GitHub soft-wraps it — `--keep-line-breaks` opts out.)

## Safety properties you can rely on

- Re-running `make populate` never duplicates: existing labels (by
  name), milestones (by title), and issues (by `[MN-k]` title prefix or
  recorded `(#N)`) are skipped.
- An interrupted populate is rerun-safe: every created issue's number is
  persisted to the file before the next API call.
- Existing labels are never overwritten, even when the plan disagrees
  on color or description — differences are reported for a human.

## Pitfalls

- `gh` must be authenticated (`gh auth status`); the target repo must
  already exist on GitHub.
- In environments that authenticate via `GH_TOKEN`/`GITHUB_TOKEN`
  (GitHub Actions), set `NO_ENV_PREFIX=1` on the make invocation — by
  default the scripts strip those variables to stop them shadowing a
  keychain token.
- Never remove the `[MN-k]` prefix from issue titles on GitHub; it is
  the join key for idempotency and for update.
