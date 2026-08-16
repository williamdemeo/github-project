# File: Makefile
#
# The primary UX for the plan-file workflow.  `make help` (the default)
# lists the targets; `make populate` / `make update` are the two
# directions of the contract stated in the README.
#
# The engine (gh_project_*.py) has exactly one home — the
# williamdemeo/github-project repository — and this Makefile finds it
# through whichever channel is available, in order:
#
#   local     scripts/gh_project_populate.py exists in THIS tree — the
#             engine repository itself, or a template copy that has not
#             yet run `make init`
#   path      gh-project-* CLIs on PATH — a Nix dev shell with the
#             engine package (see templates/consumer/flake.nix), a
#             `nix profile install`, or a future pipx install
#   checkout  GHPROJECT_DIR points at a checkout of the engine repo
#
# Variables:
#   PLAN           plan file            (default: docs/GITHUB_PROJECT.md)
#   REPO           owner/name override  (default: the plan file's
#                                        **Repository**: header)
#   PYTHON         interpreter          (default: python3)
#   GHPROJECT_DIR  engine checkout path (checkout channel only)
#   NO_ENV_PREFIX  set (to anything) wherever gh authenticates *through*
#                  GH_TOKEN/GITHUB_TOKEN — GitHub Actions included; by
#                  default the engine strips those variables so they
#                  cannot shadow a keychain-stored token.

PYTHON ?= python3
PLAN   ?= docs/GITHUB_PROJECT.md
REPO   ?=
GHPROJECT_DIR ?=

FLAGS := $(if $(REPO),--repo $(REPO)) $(if $(NO_ENV_PREFIX),--no-env-prefix)

ifneq (,$(wildcard scripts/gh_project_populate.py))
  ENGINE_MODE := local
  POPULATE := $(PYTHON) scripts/gh_project_populate.py
  UPDATE   := $(PYTHON) scripts/gh_project_update.py
  LINT     := $(PYTHON) scripts/gh_project_lint.py
else ifneq (,$(shell command -v gh-project-update 2>/dev/null))
  ENGINE_MODE := path
  POPULATE := gh-project-populate
  UPDATE   := gh-project-update
  LINT     := gh-project-lint
else ifneq (,$(wildcard $(GHPROJECT_DIR)/scripts/gh_project_populate.py))
  ENGINE_MODE := checkout
  POPULATE := $(PYTHON) $(GHPROJECT_DIR)/scripts/gh_project_populate.py
  UPDATE   := $(PYTHON) $(GHPROJECT_DIR)/scripts/gh_project_update.py
  LINT     := $(PYTHON) $(GHPROJECT_DIR)/scripts/gh_project_lint.py
else
  ENGINE_MODE := missing
endif

.PHONY: help populate-dry populate update update-check lint test init \
        guard-gh guard-engine engine-mode
.DEFAULT_GOAL := help

help: ## Show this help
	@grep -hE '^[a-zA-Z][a-zA-Z0-9_-]*:.*## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*## "} {printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'

populate-dry: guard-engine guard-gh ## Preview what populate would create (no mutations)
	$(POPULATE) $(PLAN) $(FLAGS) --dry-run

populate: guard-engine guard-gh ## Create labels, milestones, issues on GitHub from $(PLAN)
	$(POPULATE) $(PLAN) $(FLAGS)

update: guard-engine guard-gh ## Rewrite $(PLAN)'s generated regions from live GitHub state
	$(UPDATE) $(PLAN) $(FLAGS)

update-check: guard-engine guard-gh ## Report whether $(PLAN) is stale without writing
	$(UPDATE) $(PLAN) $(FLAGS) --check

lint: guard-engine ## Validate $(PLAN)'s structure (no network)
	$(LINT) $(PLAN)

test: ## Run the engine test suite (offline; engine repo / pre-init copies only)
ifneq (,$(wildcard scripts/tests))
	$(PYTHON) -m unittest discover -s scripts/tests
else
	@echo "No local engine copy: the engine's tests run in its own repository"
	@echo "(https://github.com/williamdemeo/github-project)."
endif

init: ## Detach this template copy from the engine (one-time, consumer projects)
ifneq ($(ENGINE_MODE),local)
	@echo "Nothing to do: this tree carries no local engine copy."
else
	@echo "This removes the copied engine from this tree.  The engine's home is"
	@echo "https://github.com/williamdemeo/github-project; after this, make"
	@echo "resolves it from PATH (Nix dev shell) or GHPROJECT_DIR (checkout)."
	@if [ -z "$(INIT_YES)" ]; then \
	  printf 'Continue? [y/N] '; read ans; \
	  case "$$ans" in y|Y|yes) ;; *) echo "Aborted."; exit 1;; esac; \
	fi
	rm -f scripts/gh_project_populate.py scripts/gh_project_update.py \
	      scripts/gh_project_lint.py scripts/_gh_project_lib.py \
	      scripts/VERSION
	rm -rf scripts/_utils scripts/tests scripts/__pycache__
	@if [ -f templates/consumer/flake.nix ]; then \
	  mv templates/consumer/flake.nix flake.nix; \
	  rm -f flake.lock; \
	  rm -rf templates; \
	  echo "Installed the consumer flake (engine as a pinned input)."; \
	fi
	@echo
	@echo "Done.  Next steps:"
	@echo "  1. nix flake lock       # pin the engine (Nix channel), or set"
	@echo "     GHPROJECT_DIR to an engine checkout instead"
	@echo "  2. edit $(PLAN), then: make lint && make populate-dry"
	@echo "  3. commit the result"
endif

engine-mode: ## Print which engine channel this invocation resolved (debugging)
	@echo "$(ENGINE_MODE)"

guard-engine:
ifeq ($(ENGINE_MODE),missing)
	@echo "error: the github-project engine was not found.  Provide it via one of:"; \
	echo "  - Nix: enter the dev shell (nix develop) wired by your flake's"; \
	echo "    github-project input, so the gh-project-* CLIs are on PATH"; \
	echo "  - checkout: clone https://github.com/williamdemeo/github-project"; \
	echo "    and pass GHPROJECT_DIR=/path/to/that/clone"; \
	exit 1
endif

guard-gh:
	@command -v gh >/dev/null || { \
	  echo "error: the GitHub CLI (gh) is required for this target."; \
	  echo "       https://cli.github.com/  (or: nix develop)"; \
	  exit 1; \
	}
