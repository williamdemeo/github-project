#!/usr/bin/env bash
#
# File: scripts/ci/project-plan-update.sh
#
# Keep docs/GITHUB_PROJECT.md fresh from GitHub Actions.
#
# This lives here rather than inside a workflow `run:` block because
# shell in YAML cannot be run locally, cannot be linted, and cannot be
# tested; the workflow holds only what genuinely must be there (when it
# runs, what it may access, and what it calls).
#
# Three modes, selected by $PROJECT_PLAN_AUTO_UPDATE (unset, "commit",
# or "pr"):
#
#   (unset)   Report only.  Drift is written to the step summary; the
#             job always succeeds.  This is the shipped default: a
#             template consumer gets a weekly freshness signal and no
#             bot commits unless they explicitly arm one of the modes
#             by setting the repository variable.
#   commit    Run the update and push the result straight to the
#             current branch.
#   pr        Run the update on a fixed branch (project-plan-update)
#             and open a pull request (or update the open one).
#             Requires "Allow GitHub Actions to create and approve pull
#             requests" in the repository's Actions settings.
#
# The distinction that matters is drift versus a check that never ran.
# gh_project_update.py follows diff(1) — 0 current, 1 differs, 2 failed
# — and reporting "the plan has drifted" when the real problem was an
# expired token sends you to fix the wrong thing.
#
# Environment:
#   REPO                      owner/name    (default: this repository)
#   PLAN                      plan file     (default: docs/GITHUB_PROJECT.md)
#   PROJECT_PLAN_AUTO_UPDATE  "", commit, or pr
#   NO_ENV_PREFIX             set to pass --no-env-prefix; required
#                             wherever gh authenticates through
#                             GH_TOKEN/GITHUB_TOKEN, GitHub Actions
#                             included
#   UPDATE                    the update command, overridable so the
#                             reporting logic can be exercised without
#                             the GitHub API
#
# Writes to $GITHUB_STEP_SUMMARY when set, otherwise to stdout, so
# running it locally shows exactly what CI would report.  Exit is 0 on
# every advisory path; only a failed commit/pr action exits nonzero.

# The backticks throughout the printf strings are markdown code spans
# destined for the step summary, not command substitutions.
# shellcheck disable=SC2016

set -uo pipefail

REPO="${REPO:-${GITHUB_REPOSITORY:-}}"
PLAN="${PLAN:-docs/GITHUB_PROJECT.md}"
MODE="${PROJECT_PLAN_AUTO_UPDATE:-}"
UPDATE="${UPDATE:-python3 scripts/gh_project_update.py}"
SUMMARY="${GITHUB_STEP_SUMMARY:-/dev/stdout}"
BRANCH="project-plan-update"

flags=()
[ -n "${REPO}" ] && flags+=(--repo "$REPO")
[ -n "${NO_ENV_PREFIX:-}" ] && flags+=(--no-env-prefix)

# ${flags[@]+...} guards the empty-array expansion under `set -u`, which
# is an error on bash before 4.4 (still the system bash on macOS).
out="$($UPDATE "$PLAN" --check ${flags[@]+"${flags[@]}"} 2>&1)"
code=$?

case "$code" in
  0)
    printf '`%s` is current.\n' "$PLAN" >> "$SUMMARY"
    exit 0
    ;;
  1)
    : # drift — handled below according to $MODE
    ;;
  *)
    {
      printf '### The freshness check could not run\n\n'
      printf 'This is **not** drift: the check itself failed with exit code'
      printf ' %s, so whether the plan is current is unknown.\n' "$code"
      printf 'Usually authentication or a GitHub API error.\n\n'
      printf '```\n%s\n```\n' "$out"
    } >> "$SUMMARY"
    exit 0
    ;;
esac

run_update() {
  $UPDATE "$PLAN" ${flags[@]+"${flags[@]}"}
}

case "$MODE" in
  commit)
    run_update || exit 1
    git config user.name "github-actions[bot]"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
    git add "$PLAN"
    git commit -m "Update $PLAN from live GitHub state" || exit 1
    git push || exit 1
    printf '### `%s` had drifted\n\nUpdated and pushed directly (mode: commit).\n' "$PLAN" >> "$SUMMARY"
    ;;
  pr)
    run_update || exit 1
    git config user.name "github-actions[bot]"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
    git checkout -B "$BRANCH"
    git add "$PLAN"
    git commit -m "Update $PLAN from live GitHub state" || exit 1
    git push --force origin "$BRANCH" || exit 1
    if [ -z "$(gh pr list --head "$BRANCH" --state open --json number --jq '.[].number')" ]; then
      gh pr create \
        --head "$BRANCH" \
        --title "Update $PLAN from live GitHub state" \
        --body "Automated freshness update: the generated regions of \`$PLAN\` drifted from live GitHub state.  Only content between BEGIN/END GENERATED markers changes." \
        || exit 1
    fi
    printf '### `%s` had drifted\n\nOpened/updated the `%s` pull request (mode: pr).\n' "$PLAN" "$BRANCH" >> "$SUMMARY"
    ;;
  *)
    {
      printf '### `%s` has drifted\n\n' "$PLAN"
      printf 'Run `make update` locally and commit the result — or set the\n'
      printf 'repository variable `PROJECT_PLAN_AUTO_UPDATE` to `pr` or\n'
      printf '`commit` to let this workflow do it for you.\n'
    } >> "$SUMMARY"
    ;;
esac

exit 0
