# CLAUDE.md

This repository is a **template** for running a GitHub project roadmap
from one markdown file.  If you are reading this inside a project
created from the template, the file to care about is
`docs/GITHUB_PROJECT.md`.

## The contract (one source of truth per concern)

| Concern | Source of truth | Who edits it |
| --- | --- | --- |
| STRUCTURE: milestones, labels, issue definitions, prose, dependency graphs | `docs/GITHUB_PROJECT.md` | humans and Claude, in the file |
| STATE: issue numbers, open/closed, assignees | GitHub | the normal GitHub workflow |

Two commands move information between them:

- `make populate` — file → GitHub.  Creates labels, milestones, issues;
  writes each new issue number back into the file immediately.
  Idempotent: re-running skips everything that already exists.
- `make update` — GitHub → file.  Rewrites ONLY the regions between
  `<!-- BEGIN GENERATED: ... -->` / `<!-- END GENERATED: ... -->`
  markers; hand-written prose is preserved byte-for-byte.

Corollaries for editing:

- Prose outside the markers: edit in the file, freely.
- Issue bodies after bootstrap: edit ON GITHUB (in-file edits inside a
  generated region are overwritten by the next `make update`).
- Never strip the `[MN-k]` prefix from issue titles on GitHub — it is
  the join key for idempotency and for update.

## Working here

- `make help` lists every target.  `make lint` (structure, no network)
  and `make test` (offline suite against a recorded fake `gh`) must
  pass before any PR.
- Two committed skills cover the workflows: `project-setup` (bootstrap a
  new project from the plan file) and `roadmap-upkeep` (keep the file
  and GitHub in sync, add issues/milestones to a live project).  Prefer
  them over improvising.
- Python here follows a functional house style: total functions,
  `Result`-based error handling (never exceptions for control flow),
  frozen dataclasses, pure parsing/planning core with `gh` calls at the
  edges, a `File:` header docstring in every file, and a test for every
  pure function.  Match it.
- The scripts directory is self-contained on purpose (vendored `_utils`,
  `scripts/VERSION`): downstream projects re-vendor it wholesale.  Do
  not add imports that reach outside `scripts/`, and bump
  `scripts/VERSION` when its behavior changes.
