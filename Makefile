# File: Makefile
#
# The primary UX for this template.  `make help` (the default) lists the
# targets; `make populate` / `make update` are the two directions of the
# contract stated in the README.
#
# Variables:
#   PLAN           plan file            (default: docs/GITHUB_PROJECT.md)
#   REPO           owner/name override  (default: the plan file's
#                                        **Repository**: header)
#   PYTHON         interpreter          (default: python3)
#   NO_ENV_PREFIX  set (to anything) wherever gh authenticates *through*
#                  GH_TOKEN/GITHUB_TOKEN — GitHub Actions included; by
#                  default the scripts strip those variables so they
#                  cannot shadow a keychain-stored token.

PYTHON ?= python3
PLAN   ?= docs/GITHUB_PROJECT.md
REPO   ?=

FLAGS := $(if $(REPO),--repo $(REPO)) $(if $(NO_ENV_PREFIX),--no-env-prefix)

.PHONY: help populate-dry populate update update-check lint test guard-gh
.DEFAULT_GOAL := help

help: ## Show this help
	@grep -hE '^[a-zA-Z][a-zA-Z0-9_-]*:.*## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*## "} {printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'

populate-dry: guard-gh ## Preview what populate would create (no mutations)
	$(PYTHON) scripts/gh_project_populate.py $(PLAN) $(FLAGS) --dry-run

populate: guard-gh ## Create labels, milestones, issues on GitHub from $(PLAN)
	$(PYTHON) scripts/gh_project_populate.py $(PLAN) $(FLAGS)

update: guard-gh ## Rewrite $(PLAN)'s generated regions from live GitHub state
	$(PYTHON) scripts/gh_project_update.py $(PLAN) $(FLAGS)

update-check: guard-gh ## Report whether $(PLAN) is stale (exit 1) without writing
	$(PYTHON) scripts/gh_project_update.py $(PLAN) $(FLAGS) --check

lint: ## Validate $(PLAN)'s structure (no network)
	$(PYTHON) scripts/gh_project_lint.py $(PLAN)

test: ## Run the test suite (no network; uses a recorded fake gh)
	$(PYTHON) -m unittest discover -s scripts/tests

guard-gh:
	@command -v gh >/dev/null || { \
	  echo "error: the GitHub CLI (gh) is required for this target."; \
	  echo "       https://cli.github.com/  (or: nix develop)"; \
	  exit 1; \
	}
