<!-- File: README.md -->

# github-project

A template for creating and working on GitHub projects, especially (though
not exclusively) useful for AI-assisted projects.

One markdown file — `docs/GITHUB_PROJECT.md` — holds your roadmap: milestones,
labels, and issues, written by hand (or by your AI assistant) before anything
exists on GitHub.  Two commands keep that file and GitHub in sync, in opposite
directions:

| Command         | Direction     | What moves                                                        |
| --------------- | ------------- | ----------------------------------------------------------------- |
| `make populate` | file → GitHub | creates labels, milestones, issues; writes issue numbers back into the file |
| `make update`   | GitHub → file | rewrites the *generated regions* with live issue state; hand-written prose is never touched |

("Update" names the user's intent — bring `docs/GITHUB_PROJECT.md` up to
date — not a push to GitHub.  The push direction is `populate`.)

## The contract

One source of truth per concern:

| Concern                                                                  | Source of truth          | Who edits it              |
| ------------------------------------------------------------------------ | ------------------------ | ------------------------- |
| **Structure**: milestones, labels, issue definitions, prose, dependency graphs | `docs/GITHUB_PROJECT.md` | humans (and Claude)       |
| **State**: issue numbers, open/closed, assignees                          | GitHub                   | GitHub UI / normal workflow |

`populate` pushes structure to GitHub once, at bootstrap; it is idempotent, so
re-running it never duplicates anything.  `update` pulls state back into the
file for as long as the project lives, rewriting only the regions between
`<!-- BEGIN GENERATED: ... -->` / `<!-- END GENERATED: ... -->` markers.
After bootstrap, issue bodies live on GitHub like any other issue; the
generated regions mirror them.

## Status

Under construction — the scripts, Makefile, worked example, and CI are landing
via pull request.  This README will grow a quickstart once every command in it
has been executed as written.
