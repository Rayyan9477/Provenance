# =============================================================================
# Provenance - task runner
#
# Every command the design pack names lives here, so that a gate reviewer runs
# `make gate-<N>` rather than reconstructing a command from prose.
#
# Target inventory and their authorities:
#   quality/23_PHASE_GATES.md section 6   bootstrap lint test db-migrate
#                                         db-verify seed seed-perturb sabotage
#                                         gate-0 .. gate-15
#   quality/20_TDD_STRATEGY.md section 3.3 test-fast test-db test-all
#                             section 14.4 test-submission
#   EXECUTION/72_DEFECT_PROTOCOL.md 11.3   defects debt close-proof triage-round
#   ops/41_RUNBOOK.md section 0.1          probe run-api run-web run-crdb
#                                         run-sink embeddings-warm demo-reset
#                                         demo-rehearse
#
# THE RULE THAT GOVERNS THIS FILE
# ------------------------------------------------------------------
# A target whose implementation has not been built yet prints the phase that
# owns it and exits NON-ZERO. It never succeeds quietly. A gate target that
# passes without running its assertions is the vacuity failure that
# quality/23_PHASE_GATES.md section 23 and EXECUTION/72_DEFECT_PROTOCOL.md are
# written to prevent, and it is worse than no target at all because it produces
# a green log.
#
# This file is Integrator-owned. EXECUTION/71_AGENT_WORKFLOW.md section 7 lists
# it among the files no Builder agent may edit.
# =============================================================================

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
MAKEFLAGS += --no-print-directory

PY     ?= python
PIP    ?= $(PY) -m pip
PYTEST ?= $(PY) -m pytest
RUFF   ?= $(PY) -m ruff
MYPY   ?= $(PY) -m mypy

# tools/gate.sh is created by T0.3. Every gate assertion is captured through it
# so that the log in ops/gates/logs/ is scrubbed and carries assertion id, git
# sha, timestamp and exit code.
GATE := tools/gate.sh

PACKAGES := packages/python/provenance_contracts \
            packages/python/provenance_domain \
            packages/python/provenance_db \
            packages/python/provenance_telemetry

# The two packages the commit lane type-checks (20_TDD_STRATEGY.md 14.2).
TYPED := packages/python/provenance_domain packages/python/provenance_contracts

# The commit lane selection from 20_TDD_STRATEGY.md section 14.1.
COMMIT_LANE := unit or contract or adversarial or (retrieval and not slow) or db or (concurrency and not slow)

# ops/41_RUNBOOK.md section 1: uv is optional and bootstrap falls back to pip.
INSTALLER := $(shell command -v uv >/dev/null 2>&1 && echo "uv pip" || echo "$(PY) -m pip")

# -----------------------------------------------------------------------------
# The not-yet-implemented reporter.
#   $(call unimplemented,<phase number>,<owning task and one line of scope>)
# Do not put a comma in either argument: make would read it as a third.
# -----------------------------------------------------------------------------
define unimplemented
	@printf '\n  make %s is not implemented until Phase %s.\n' "$@" "$(1)" >&2
	@printf '  Owner: %s\n' "$(2)" >&2
	@printf '  Exiting non-zero on purpose. A target that succeeds without doing\n' >&2
	@printf '  its work produces a green log for work that never happened which is\n' >&2
	@printf '  the exact failure quality/23_PHASE_GATES.md section 23 describes.\n\n' >&2
	@exit 1
endef

# Refuse to run a gate battery on a make that silently drops .SHELLFLAGS.
#
# On GNU Make 3.81 the .SHELLFLAGS assignment on line 32 is ignored outright --
# not warned about, ignored. Every multi-command recipe below then loses -e, -u
# and pipefail, so a failing assertion in the middle of a recipe scrolls past
# and the recipe still exits 0. A gate battery is the one place in this
# repository where that is intolerable: the output is the evidence, and
# evidence that cannot distinguish pass from fail is worse than no evidence,
# because it is filed as a pass.
#
# `bootstrap` has carried this guard since T0.3. The gate targets did not, which
# meant the check protected the install and not the thing being certified.
# GnuWin32 ships 3.81 and is first on PATH on the build machine; ezwinports
# make-4.4.1 is the drop-in that works. (D-00-023.)
define require_make_version
	@$(PY) -c 'v="$(MAKE_VERSION)".split("."); \
	  raise SystemExit(0 if (int(v[0]), int(v[1] if len(v)>1 else 0)) >= (3, 82) else 1)' || { \
	  printf '\n  Refusing to run a gate battery on GNU Make $(MAKE_VERSION).\n' >&2; \
	  printf '  .SHELLFLAGS (line 32) requires >= 3.82. On 3.81 it is silently ignored,\n' >&2; \
	  printf '  every multi-command recipe loses -e -u and pipefail, and a failing\n' >&2; \
	  printf '  assertion exits 0. The battery would report a pass it did not measure.\n' >&2; \
	  printf '  Install ezwinports make 4.4.1 and put it ahead of GnuWin32 on PATH.\n\n' >&2; \
	  exit 1; }
endef

# Refuse to pretend a gate ran when the capture harness does not exist yet.
define require_gate_sh
	@test -f $(GATE) || { \
	  printf '\n  %s does not exist yet - it is created by T0.3 (gate tooling).\n' "$(GATE)" >&2; \
	  printf '  Gate assertions are not run without it because an uncaptured\n' >&2; \
	  printf '  assertion produces no evidence and quality/23_PHASE_GATES.md section 3\n' >&2; \
	  printf '  forbids reporting a step complete without pasted output.\n\n' >&2; \
	  exit 1; }
endef

.PHONY: help bootstrap lint test test-fast test-db test-all test-submission \
        probe db-probe seed seed-perturb db-migrate db-verify db-reset \
        demo-reset demo-rehearse sabotage \
        run-api run-web run-crdb run-sink stop-local embeddings-warm \
        defects debt close-proof triage-round \
        clean \
        gate-0 gate-1 gate-2 gate-3 gate-4 gate-5 gate-6 gate-7 \
        gate-8 gate-9 gate-10 gate-11 gate-12 gate-13 gate-14 gate-15

# =============================================================================
# Help
# =============================================================================

help:                   ## Print every target with its one-line description.
	@printf 'Provenance - make targets\n\n'
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'
	@printf '\nTargets marked (Phase N) exit non-zero until Phase N builds them.\n'

# =============================================================================
# Build, lint, test - implemented now
# =============================================================================

bootstrap:              ## Install the four provenance_* packages plus the dev toolchain.
	@$(PY) -c 'import sys; v="$(MAKE_VERSION)".split("."); \
	  raise SystemExit(0 if (int(v[0]), int(v[1] if len(v)>1 else 0)) >= (3, 82) else 1)' || { \
	  printf 'GNU Make >= 3.82 is required. Found: $(MAKE_VERSION)\n' >&2; \
	  printf '.SHELLFLAGS (line 32) was introduced in 3.82. On 3.81 it is silently\n' >&2; \
	  printf 'ignored, so every multi-command recipe in this file loses -e, -u and\n' >&2; \
	  printf 'pipefail and a failing step scrolls past with exit 0.\n' >&2; \
	  printf 'Git for Windows ships GNU Make 4.x: put its bin directory ahead of\n' >&2; \
	  printf 'any GnuWin32 make on PATH. (D-00-005.)\n' >&2; \
	  exit 1; }
	@$(PY) -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' || { \
	  printf 'Python 3.12.x is required (ops/41_RUNBOOK.md section 1). Found: ' >&2; \
	  $(PY) --version >&2; \
	  printf 'Not 3.13: provenance_contracts targets 3.12 typing and the AgentCore\n' >&2; \
	  printf 'and LangGraph pins are validated on 3.12.\n' >&2; \
	  exit 1; }
	$(INSTALLER) install --upgrade pip
	$(INSTALLER) install $(foreach p,$(PACKAGES),-e $(p))
	$(INSTALLER) install -r requirements-dev.txt
	@if [ -f apps/web/package.json ]; then \
	  npm --prefix apps/web ci; \
	 else \
	  printf 'apps/web/package.json does not exist yet (Phase 12) - skipping npm ci.\n'; \
	 fi
	@if [ ! -f .pre-commit-config.yaml ]; then \
	  printf '.pre-commit-config.yaml does not exist yet (T0.7) - skipping hook install.\n'; \
	 elif ! command -v pre-commit >/dev/null 2>&1; then \
	  printf 'pre-commit is not on PATH although .pre-commit-config.yaml exists.\n' >&2; \
	  printf 'It is in the dev extra; this install should have provided it.\n' >&2; \
	  exit 1; \
	 else \
	  pre-commit install; \
	 fi
# G0.3 and S8 both assert `gitleaks detect`. A machine without the binary
# cannot run either, which silently promotes tools/scrub.py from the first of
# two filters to the only one. Fail here rather than at the gate. (D-00-005.)
	@command -v gitleaks >/dev/null 2>&1 || { \
	  printf '\n  gitleaks is not on PATH. G0.3 and S8 both run it and cannot be\n' >&2; \
	  printf '  reported without it (.gitleaks.toml pins >= 8.30.0; below that the\n' >&2; \
	  printf '  allowlists are silently inert for every custom pv-* rule).\n' >&2; \
	  printf '  Install: https://github.com/gitleaks/gitleaks/releases (8.30.1 is\n' >&2; \
	  printf '  the version .github/workflows/ci.yml pins).\n\n' >&2; \
	  exit 1; }
	@gitleaks version
	@printf '\nbootstrap complete. Next: make lint\n'

lint:                   ## ruff + mypy --strict + import-linter contracts.
	$(RUFF) check .
	$(RUFF) format --check .
	$(MYPY) --strict $(TYPED)
#
# There is deliberately NO fallback invocation here. The obvious one,
# `$(PY) -m importlinter.cli lint-imports`, is a silent no-op that exits 0:
# importlinter/cli.py declares its click commands at module scope and has no
# `if __name__ == "__main__"` block, and the package ships no __main__.py, so
# running it as a module evaluates ZERO contracts and returns 0. That branch
# made `make lint` green on every machine without the console script - E1
# kernel-purity included, which ci.yml calls "the one this project cannot ship
# without". A missing tool must be loud. (D-00-005.)
#
# The console script is preferred, but on this build machine it is present and
# NOT EXECUTABLE from Git Bash - the anaconda Scripts shim gives "Permission
# denied" - so `command -v` finds it and running it fails with exit 126.
# Refusing outright would make E1 unrunnable here; falling back to the module
# form would make it silently vacuous. Neither is acceptable, so there is a
# third invocation: importing the click command and calling it evaluates every
# contract properly.
#
# Whichever path is taken the output is then CHECKED for the summary line, and
# that check is the load-bearing part: it is what makes a vacuous run - zero
# contracts, exit 0 - fail instead of pass. Measured against this tree:
#   lint-imports, function form  ->  "Contracts: 4 kept, 0 broken."   exit 0
#   python -m importlinter.cli   ->  no contract lines at all,        exit 0
	@out=$$( if command -v lint-imports >/dev/null 2>&1 && lint-imports --help >/dev/null 2>&1; then lint-imports 2>&1; else $(PY) -c 'from importlinter.cli import lint_imports_command; lint_imports_command()' 2>&1; fi ); rc=$$?; \
	printf '%s\n' "$$out"; \
	printf '%s' "$$out" | grep -qE '^Contracts: [0-9]+ kept' || { \
	  printf '\n  import-linter produced no contract summary: it evaluated NOTHING.\n' >&2; \
	  printf '  A green log here would cover E1 kernel purity, which ci.yml calls\n' >&2; \
	  printf '  "the one this project cannot ship without". Install import-linter\n' >&2; \
	  printf '  (make bootstrap) and re-run. (D-00-007.)\n\n' >&2; \
	  exit 1; }; \
	exit $$rc
# Phase 3 (T3.4). A transaction callback that makes a model or network call
# holds a SERIALIZABLE transaction open across an unbounded wait, and the retry
# loop then replays that side effect. The rule is unenforceable by review, so it
# is a lint. G3.5 asserts its output.
	$(PY) -m tools.txn_purity_lint services packages workers
# Later phases add their linters to this target rather than to a private script:
#   Phase 1  tools/contract_lint.py      (no-float-money / schema-version-present)
#   Phase 3  tools/txn_purity_lint.py    WIRED IN below, 2026-08-18
#   Phase 4  tools/write_path_lint.py    (canonical writes only from memory_kernel)
#   Phase 5  tools/log_schema_lint.py    (21_OBSERVABILITY_ANALYTICS.md section 12)
#   Phase 15 scripts/check_vocabulary.py (Provenance / grounding / lineage)

test:                   ## The commit lane from 20_TDD_STRATEGY.md 14.1.
	$(PYTEST) -q -m "$(COMMIT_LANE)"

test-fast:              ## L1 only - hermetic; no database, no network, no credentials.
	$(PYTEST) -q -m unit

test-db:                ## L2 - requires a CockroachDB cluster (PROVENANCE_TEST_DB_URL).
	$(PYTEST) -q -m "db and not slow"

test-all:               ## Every layer including live_model. Needs a cluster and Bedrock.
	$(PYTEST) -q

probe:                  ## Run the Phase 0 capability probes; writes ops/*.txt transcripts.
	@if [ -f ops/probes/phase0-probe.sh ]; then \
	  bash ops/probes/phase0-probe.sh; \
	 elif command -v pwsh >/dev/null 2>&1 && [ -f ops/probes/phase0-probe.ps1 ]; then \
	  pwsh -NoProfile -File ops/probes/phase0-probe.ps1; \
	 elif command -v powershell >/dev/null 2>&1 && [ -f ops/probes/phase0-probe.ps1 ]; then \
	  powershell -NoProfile -File ops/probes/phase0-probe.ps1; \
	 else \
	  printf '\n  Neither ops/probes/phase0-probe.sh nor a PowerShell host with\n' >&2; \
	  printf '  ops/probes/phase0-probe.ps1 was found. T0.6 fills both scripts.\n\n' >&2; \
	  exit 1; \
	 fi

db-probe: probe         ## Alias for `probe` - the name submission/50_README_DRAFT.md uses.

run-crdb:               ## Local single-node CockroachDB for CI parity (console on :8081).
	docker run -d --name pv-crdb \
	  -p 26257:26257 -p 8081:8080 \
	  cockroachdb/cockroach:latest-v25.3 start-single-node --insecure
	@printf 'SQL: postgresql://root@localhost:26257/provenance?sslmode=disable\n'
	@printf 'Console is on 8081 - the control plane owns 8080.\n'

run-sink:               ## Local mail sink: SMTP :1025, UI http://localhost:8025.
	docker run -d --name pv-sink -p 1025:1025 -p 8025:8025 axllent/mailpit
	@printf 'Never point SES_TRANSPORT at a real mailbox for a demo.\n'

stop-local:             ## Stop and remove the local CockroachDB and mail-sink containers.
	-docker rm -f pv-crdb
	-docker rm -f pv-sink

clean:                  ## Remove caches and build output. Touches no committed file.
	rm -rf .pytest_cache .mypy_cache .ruff_cache .import_linter_cache build
	find . -type d -name __pycache__ -not -path './.git/*' -prune -exec rm -rf {} +

# =============================================================================
# Defect protocol - EXECUTION/72_DEFECT_PROTOCOL.md section 11.3, reproduced.
#
# tools/defect_lint.py and tools/close_proof.py are created by T0.3. Until then
# these targets fail with a ModuleNotFoundError, which is the correct outcome:
# section 11.3 makes a defect-ledger check a binding precondition of every gate
# verdict, and a precondition that no task creates is a check that silently
# never runs.
# =============================================================================

defects:                ## Print the ledger grouped by status. PHASE=<N> filters to one phase.
	$(PY) -m tools.defect_lint --report $(if $(PHASE),--phase $(PHASE),)

debt:                   ## Print the open carried-debt ledger for the gate report.
	$(PY) -m tools.defect_lint --debt --check-escalation

close-proof:            ## make close-proof ID=D-04-002
	$(PY) -m tools.close_proof $(ID)

triage-round:           ## make triage-round PHASE=4 - merge inbox files report collisions.
	$(PY) -m tools.defect_lint --merge-inbox --phase $(PHASE)

# =============================================================================
# Not yet implemented - each names the phase that owns it
# =============================================================================

db-migrate:             ## (Phase 2) alembic upgrade head against the target database.
	$(call unimplemented,2,T2.1 through T2.6 - the 0001..0008 migration chain)

db-verify:              ## (Phase 2) Run db/verify.sql - the V1..V11 verification queries.
	$(call unimplemented,2,T2.7 - db/verify.sql from specs/10_DATABASE_DDL.md section 18)

db-reset:               ## (Phase 2) Drop and recreate the provenance_ci database.
	$(call unimplemented,2,T2.1 - destructive reset of the CI database only)

seed:                   ## (Phase 2) Load the hero corpus and the 18000 decoys.
	$(call unimplemented,2,T2.8 - deterministic seed plus db/seeds/MANIFEST.json)

seed-perturb:           ## (Phase 2) Reseed with the outcome-bearing rows removed or shifted.
	$(call unimplemented,2,T2.8 - the detector for a demo that passes on seeded state)

demo-reset:             ## (Phase 2) Destructive reset to clean demo state.
	$(call unimplemented,2,T2.8 - drop/recreate then migrate; reseeding stays separate)

embeddings-warm:        ## (Phase 6) Populate the embedding cache without touching the database.
	$(call unimplemented,6,T6.1 - Titan embeddings and db/seeds/vectors.parquet)

run-api:                ## (Phase 8) Control plane on :8080.
	$(call unimplemented,8,T8.1 - services/control_plane/app/main.py)

run-web:                ## (Phase 12) Next.js on :3000.
	$(call unimplemented,12,T12.1 - apps/web)

sabotage:               ## (Phase 14) Neuter each symbol in the matrix; assert its tests go red.
	$(call unimplemented,14,T14.6 - tests/sabotage_matrix.yaml reaches 18 entries)

demo-rehearse:          ## (Phase 15) Scripted dress rehearsal.
	$(call unimplemented,15,T15.9 - ops/41_RUNBOOK.md section 8.1)

test-submission:        ## (Phase 15) Everything twice plus the Definition of Done checklist.
	$(call unimplemented,15,T15.8 - the 18-assertion checklist runner from 06 section 20)

# =============================================================================
# Gate batteries
#
# gate-0 is real: Phase 0 is the phase being built. It is RED until T0.2 writes
# LICENSE, T0.3 writes tools/gate.sh, T0.4 writes the settings object and T0.6
# writes the probe transcripts - which is the intended order. A gate that is
# green before its phase is built is measuring nothing.
# =============================================================================

gate-0:                 ## G0.1..G0.7 - scaffold licence settings and cluster verification.
	$(call require_make_version)
	$(call require_gate_sh)
	@printf '\n=== G0.1  Apache-2.0 licence present and is actually Apache-2.0 ===\n'
	$(GATE) G0.1 -- bash -c 'set -euo pipefail; \
	  head -3 LICENSE; \
	  actual=$$(sha256sum LICENSE | cut -d" " -f1); \
	  expected=$$(grep -oE "[0-9a-f]{64}" ops/decisions/LICENSE_SHA.txt | head -1); \
	  test -n "$$expected"; \
	  test "$$actual" = "$$expected"'
	@printf '\n=== G0.2  repository is public and GitHub agrees about the licence ===\n'
	$(GATE) G0.2 -- bash -c 'set -euo pipefail; \
	  gh repo view --json visibility,licenseInfo \
	    -q ".visibility + \" \" + .licenseInfo.spdxId" | tee /dev/stderr \
	  | grep -Fxq "PUBLIC Apache-2.0"'
	@printf '\n=== G0.3  no secrets have ever been committed ===\n'
	$(GATE) G0.3 -- gitleaks detect --source . --config .gitleaks.toml --redact --no-banner --exit-code 1
# G0.3 above is a GIT-HISTORY scan: it answers "was a secret ever committed".
# G0.3b below is a WORKING-TREE scan of ops/ and answers a different question:
# "is a secret sitting in the evidence directory right now". Both are needed and
# neither substitutes for the other.
#
# This used to read `--source ops/gates` WITHOUT --no-git, and it was measurably
# useless. Two compounding reasons:
#   1. In git mode gitleaks scans the repository containing --source and ignores
#      the path scoping entirely. The old command reported "1 commits scanned,
#      2.75 MB" - byte-identical to the G0.3 scan above it. It was never a
#      second scan; it was the same scan run twice.
#   2. ops/ is UNTRACKED, so a git-mode scan cannot see it at all.
# Demonstrated: a CockroachDB DSN carrying a real-shaped password, planted at
# ops/gates/_canary.md, produced "no leaks found" under the old command and
# "leaks found: 1" under this one. The directory .gitleaks.toml calls the
# artefacts "most likely to carry a command line with a credential" was the one
# directory neither scan could reach.
#
# --source ops (not ops/gates): the transcripts, the ledgers and the decision
# records are all equally exposed, and gate logs are not special among them.
# --source stays RELATIVE: the repo-relative `paths` regexes in .gitleaks.toml
# silently stop matching against the absolute File paths an absolute --source
# produces. Both name --config explicitly so neither can fall back to the
# default ruleset. (D-00-005, D-00-041.)
	$(GATE) G0.3b -- gitleaks detect --source ops --config .gitleaks.toml --redact --no-banner --no-git --exit-code 1
	@printf '\n=== G0.4  clean-clone bootstrap works ===\n'
	$(GATE) G0.4 -- bash -c 'set -euo pipefail; \
	  d=$$(mktemp -d); \
	  git clone "$$(gh repo view --json url -q .url)" "$$d/pv-clean"; \
	  cd "$$d/pv-clean" && make bootstrap && make lint && make test'
	@printf '\n=== G0.5  the cluster exists and answers ===\n'
	$(GATE) G0.5 -- bash -c 'set -euo pipefail; \
	  ccloud cluster list --output json | jq -r ".[] | .name + \" \" + .state"'
# The secret is resolved into the child process environment only. It never
# enters shell history, ps output or a gate log. The $$U reference is inside
# single quotes on purpose so that asm-exec's child expands it, not this shell.
	$(GATE) G0.5b -- asm-exec \
	  --env U='{{resolve:secretsmanager:provenance/db:SecretString:migrator_url}}' \
	  -- bash -c 'set -euo pipefail; \
	  cockroach sql --url "$$U" --format=csv -e "SELECT version();" \
	  | tee /dev/stderr | grep -q "CockroachDB CCL v"'
	@printf '\n=== G0.6  the vector probes were run and a variant was chosen ===\n'
	$(GATE) G0.6 -- bash -c 'set -euo pipefail; \
	  test -s ops/cluster-probe.txt; \
	  n=$$(grep -c "^-- P" ops/cluster-probe.txt); \
	  echo "-- P headers: $$n"; test "$$n" = "11"; \
	  v=$$(grep -cE "^VARIANT: (A|B|C)$$" ops/decisions/VECTOR_INDEX_VARIANT.md); \
	  echo "VARIANT lines: $$v"; test "$$v" = "1"'
	@printf '\n=== G0.7  the settings object refuses to start on a missing required variable ===\n'
	$(GATE) G0.7 -- bash -c 'set -uo pipefail; \
	  out=$$(env -i PATH="$$PATH" $(PY) -c \
	    "from provenance_contracts.settings import Settings; Settings()" 2>&1); \
	  rc=$$?; \
	  printf "%s\nexit=%s\n" "$$out" "$$rc"; \
	  test "$$rc" -ne 0 || exit 1; \
	  printf "%s" "$$out" | grep -q "COCKROACH_DATABASE_URL"'
# `|| exit 1` is load-bearing. These flags are `-uo pipefail` without `-e` on
# purpose - `rc=$$?` has to survive the failing command - and without `-e` a
# bare `test ...;` is a statement whose status is discarded. G0.7 asserts two
# things (non-zero exit AND the variable is named) and would otherwise be able
# to fail on only the second. (D-00-005.)
	@printf '\nG-0 battery complete. Record every result in ops/gates/PHASE_00.md.\n'

gate-1:                 ## (Phase 1) G1.x - contracts and domain.
	$(call unimplemented,1,T1.1 through T1.8 - provenance_contracts and provenance_domain)

gate-2:                 ## (Phase 2) G2.x - schema migrations and seed.
	$(call unimplemented,2,T2.1 through T2.8 - the 26 canonical tables and the seed)

gate-3:                 ## (Phase 3) G3.x - database runtime and retry.
	$(call unimplemented,3,T3.1 through T3.6 - pools SERIALIZABLE retry and 40001 mapping)

gate-4:                 ## (Phase 4) G4.x - the Memory Kernel.
	$(call unimplemented,4,T4.1 onward - the single canonical write path)

gate-5:                 ## (Phase 5) G5.x - deterministic read models.
	$(call unimplemented,5,T5.1 onward - State Proof grounding and lineage)

gate-6:                 ## (Phase 6) G6.x - embeddings and retrieval.
	$(call unimplemented,6,T6.1 onward - Titan embeddings and the ANN index)

gate-7:                 ## (Phase 7) G7.x - the LangGraph graphs.
	$(call unimplemented,7,T7.1 onward - ingestion and advocate graphs)

gate-8:                 ## (Phase 8) G8.x - API and auth.
	$(call unimplemented,8,T8.1 onward - the FastAPI surface and Cognito principals)

gate-9:                 ## (Phase 9) G9.x - actions approval and executor.
	$(call unimplemented,9,T9.1 onward - invariant 4 lives here)

gate-10:                ## (Phase 10) G10.x - events outbox and scheduler.
	$(call unimplemented,10,T10.1 onward - the transactional outbox and trigger wakeups)

gate-11:                ## (Phase 11) G11.x - MCP SQL roles and agent views.
	$(call unimplemented,11,T11.1 onward - pv_agent_reader and the five agent_*_v1 views)

gate-12:                ## (Phase 12) G12.x - frontend Judge Mode and counterfactual.
	$(call unimplemented,12,T12.1 onward - apps/web)

gate-13:                ## (Phase 13) G13.x - deploy.
	$(call unimplemented,13,T13.1 onward - App Runner Amplify and AgentCore)

gate-14:                ## (Phase 14) G14.x - evals adversarial and concurrency.
	$(call unimplemented,14,T14.1 onward - the 51 scenarios and the sabotage matrix)

gate-15:                ## (Phase 15) G15.x and S1..S10 - submission artifacts.
	$(call unimplemented,15,T15.1 onward - README video and disclosure)
