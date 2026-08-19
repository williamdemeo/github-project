---
name: roadmap-upkeep
description: Keep docs/GITHUB_PROJECT.md current with GitHub — pull live issue state into the file (make update), check for drift, add new issues or milestones to an already-populated project, and interpret the freshness workflow. Use for "update the roadmap", "is the plan current", "sync the project file", "add an issue to the plan", or after issues were closed/edited on GitHub.
---

# Keep the roadmap current

After bootstrap (see the project-setup skill), GitHub owns issue STATE;
`docs/GITHUB_PROJECT.md` mirrors it inside marked regions and owns the
surrounding prose.  Two commands, two directions:

| Command         | Direction     | Rewrites                          |
| --------------- | ------------- | --------------------------------- |
| `make update`   | GitHub → file | only the BEGIN/END GENERATED regions |
| `make populate` | file → GitHub | nothing in the file except `(#N)` write-backs |

## Routine refresh

- `make update-check` — is the file current?  Succeeds when current,
  fails otherwise; the output says whether the file drifted or the
  check itself failed (auth, network — NOT drift; fix the cause before
  trusting any answer).  When an exit code must distinguish drift from
  failure, call the script directly —
  `python3 scripts/gh_project_update.py docs/GITHUB_PROJECT.md --check`
  follows diff(1) (0 current / 1 drifted / 2 failed); `make` collapses
  any recipe failure to exit 2.
- `make update` — rewrite the generated regions from live GitHub state.
  Hand-written prose outside the markers is preserved byte-for-byte.
  Commit the result.

A freshly populated plan's FIRST `update --check` is always stale —
update's canonical rendering differs cosmetically from authored input
(no `**Milestone:**` lines, labels in GitHub's order, no trailing
`---`).  Run one plain `make update`, commit it as the normalization,
and only then treat `update-check` failures as drift.

## Adding work to a live project

1. New issue in an existing milestone: add a
   `### Issue MN-k: Title` block (next free k) inside that milestone's
   region, with `**Labels:**` including `milestone-N-<slug>`.  Fan-out
   sub-tickets of issue MN-k are `MN-ka`, `MN-kb`, ...
2. New milestone: add the `### Milestone N — Title` block, a
   `milestone-N-<slug>` label entry, and a new
   `<!-- BEGIN GENERATED: milestone-N -->` region for its issues.
3. `make lint`, then `make populate-dry`, then `make populate` —
   existing entities are skipped, only the additions are created.
4. `make update` to canonicalize.

Issue bodies of already-created issues are edited ON GITHUB (the region
mirror would overwrite in-file edits on the next update).  Prose outside
regions is edited in the file.

## Organically-filed issues (the `unplanned` region)

Issues filed directly on GitHub without a `[MN-k]` title prefix never
appear in milestone regions — the prefix is those regions' identifier
contract.  A region marked `<!-- BEGIN GENERATED: unplanned -->` /
`<!-- END GENERATED: unplanned -->` mirrors them instead: grouped by
GitHub milestone (title verbatim, no-milestone group last), sorted by
issue number, same block shape minus the id.  Everything inside it is
GitHub-owned mirror content — the parser and lint deliberately ignore
it, so organic titles can never inject plan structure.  To promote an
organic issue into the plan, retitle it on GitHub with the next free
`[MN-k]` prefix; the next update moves it into its milestone region.
If unplanned issues exist and the file has no such region, update warns
on stderr.

## The scheduled freshness workflow

`.github/workflows/project-plan-update.yml` runs Mondays (and on
manual dispatch).  By default it only REPORTS drift in the run's step
summary.  To let it act, set the repository variable
`PROJECT_PLAN_AUTO_UPDATE` to `pr` (opens/refreshes a pull request —
requires "Allow GitHub Actions to create and approve pull requests" in
the repo's Actions settings) or `commit` (pushes directly).  Delete the
workflow file if no scheduled runs are wanted.

## Reading update's output

- `### Issue M1-2: Title (#42)` — open issue #42.
- `### Issue M1-2: Title (#42, closed)` — closed on GitHub.
- `**Assignees**: @login` — present only when assigned.
- `_(no open or closed issues with `milestone-N-*` label)_` — the
  region exists but no issue carries its label yet.
- A `no rendering rule` comment — the region id is not `milestone-N`;
  fix the marker id.
