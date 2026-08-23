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

# T2.8 owns scripts/seed/**. Flipped from `schema-only` to `all` on 2026-08-24,
# in the same change that landed the Phase 4 kernel replay -- which is what the
# previous comment here asked for.
#
# Step 9 replays the curated MemoryProposal fixtures through
# MemoryKernel.commit() as pv_kernel_writer. It could not run before, and the
# seed did NOT work around that: seeding claims, beliefs or commitments by raw
# INSERT would create a SECOND canonical writer, which is the one thing the
# architecture forbids (70_TASK_PLAN.md T2.8 step 9). Twelve empty tables were
# the honest cost of waiting.
#
# `schema-only` still exists and still skips the replay, with a stated reason
# rather than a silent difference. db/seeds/MANIFEST.json now carries the row
# counts `all` produces, so `26 tables checked, 26 match` is a claim about a
# COMPLETE seed rather than about a known-partial one.
SEED_PROFILE ?= all
SEED_ARGS    ?=

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

# Refuse to report a test lane that never executed.
#
# pytest aborts the ENTIRE session on a single conftest ImportError -- it is not
# scoped to the broken directory. The run prints "N/M tests collected" and then
# "Interrupted: K errors during collection", and exits 2. A reader skimming for
# a number finds a plausible one, and a gate checking for exit 1 misreads it.
#
# THIS CANNOT BE A PYTEST TEST. A test inside the lane is aborted by the very
# error it exists to detect. That is not hypothetical: the first version of this
# guard carried `pytest.mark.unit`, so `pytest -q -m unit` never executed it --
# the name appeared zero times in the output -- and it only ever reported when
# invoked in a scope narrow enough to dodge the broken conftests. (D-00-044.)
#
# --collect-only performs the same collection in its own session and exits 2 on
# abort, so it fails before the lane is trusted. The status is taken from the
# command directly and never through a pipe: piping discards the exit code,
# which STATUS.md records as having produced a false green three separate times.
define require_collection
	@$(PYTEST) --collect-only -q $(1) >/dev/null 2>&1 || { \
	  printf '\n  Collection aborted. NOTHING in this lane executed.\n\n' >&2; \
	  $(PYTEST) --collect-only -q $(1) 2>&1 | grep -E '^ERROR|Interrupted|errors? in' >&2 || true; \
	  printf '\n  One conftest ImportError ends the whole pytest session, so every\n' >&2; \
	  printf '  other directory is silenced with it. This lane is not red -- it is\n' >&2; \
	  printf '  not running. Fix the import above.\n\n' >&2; \
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

.PHONY: seed-restore help bootstrap lint test test-fast test-db test-all test-submission \
        probe db-probe seed seed-perturb db-migrate db-verify db-reset \
        demo-reset demo-rehearse sabotage \
        run-api run-web run-crdb run-sink stop-local embeddings-warm \
        defects debt close-proof triage-round \
        evals \
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
	$(call require_collection,-m "$(COMMIT_LANE)")
	$(PYTEST) -q -m "$(COMMIT_LANE)"

test-fast:              ## L1 only - hermetic; no database, no network, no credentials.
	$(call require_collection,-m unit)
	$(PYTEST) -q -m unit

test-db:                ## L2 - requires a CockroachDB cluster (PROVENANCE_TEST_DB_URL).
	$(call require_collection,-m "db and not slow")
	$(PYTEST) -q -m "db and not slow"

test-all:               ## Every layer including live_model. Needs a cluster and Gemini.
	$(call require_collection,)
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

# THE DATABASE MUST BE QUIESCED. V1-V11 are whole-corpus invariants, so a
# concurrent writer makes the result meaningless in BOTH directions: a failure
# that is really someone else's half-built fixture, or a pass that read the
# database a moment before the row that would have broken it.
#
# Measured, three reads four seconds apart while a builder was committing test
# fixtures: belief_versions = 0, then 1, then 2. Two independent implementations
# of V1-V11 -- `python -m scripts.seed --verify` and this file -- appeared to
# CONTRADICT each other, one reporting all zeros and the other
# FAIL_INVARIANT on V1-V3. Neither was wrong. They read at different moments.
#
# Before treating any db-verify result as evidence, confirm nothing else is
# writing:
#   SELECT count(*) FROM [SHOW SESSIONS] WHERE application_name NOT LIKE '%psql%';
db-verify:              ## Run db/verify.sql - the V1..V11 verification queries.
# specs/10_DATABASE_DDL.md section 18 and quality/23_PHASE_GATES.md G2.5.
# db/verify.sql is ONE statement and computes its own verdict, so this target
# parses, prints and maps to an exit status - it never decides an invariant.
#   PASS / PASS_PARTIAL     0   PASS_PARTIAL names the checks that examined no rows
#   FAIL_*                  1   an invariant returned rows, or V11 < 3 with a corpus
#   VACUOUS_EMPTY_CORPUS    2   nothing was examined; V11 = 0 is correct and unproven.
#                               PV_VERIFY_ALLOW_EMPTY=1 turns this into 0 for a
#                               deliberately pre-seed database. It changes the exit
#                               status only, never the verdict text.
#   no VERDICT line         3   the file or the connection is broken
#
# Exit 2 is the point of this target. "V1..V10 returned zero rows" is trivially
# true of an empty database, and a verification suite that reports success
# against one is the vacuity failure section 23 exists to prevent. Every check
# prints the size of the population it examined, so HOLDS (zero over a non-empty
# population) is distinguishable from VACUOUS (zero over nothing).
#
# psql, not `cockroach sql`: the cockroach CLI is not installed on this machine
# and CockroachDB is wire compatible. `tr -d '\r'` because psql on Windows emits
# CRLF. Options precede -d: psql silently DROPS options placed after a
# positional dbname.
	@D="$${PV_VERIFY_URL:-}"; \
	 if [ -z "$$D" ] && [ -f .env ]; then \
	   D=$$(grep '^PROVENANCE_TEST_DB_URL=' .env | cut -d= -f2- | tr -d '\r\n' || true); \
	 fi; \
	 test -n "$$D" || { printf '\n  db-verify: no database URL. Set PV_VERIFY_URL, or PROVENANCE_TEST_DB_URL in .env.\n\n' >&2; exit 3; }; \
	 if ! out=$$(psql -X -At -v ON_ERROR_STOP=1 -d "$$D" -f db/verify.sql | tr -d '\r'); then \
	   printf '\n  db-verify: psql could not run db/verify.sql. The database must be at head first.\n\n' >&2; exit 3; \
	 fi; \
	 printf '%s\n' "$$out"; \
	 code=$$(printf '%s\n' "$$out" | sed -n 's/^VERDICT \([A-Z0-9_]*\) .*/\1/p' | head -1); \
	 case "$$code" in \
	   PASS|PASS_PARTIAL) exit 0 ;; \
	   FAIL_INVARIANT|FAIL_V11_UNDERSEEDED) \
	     printf '\n  db-verify FAILED. The verdict line above says which invariant and why.\n\n' >&2; \
	     exit 1 ;; \
	   VACUOUS_EMPTY_CORPUS) \
	     if [ "$${PV_VERIFY_ALLOW_EMPTY:-0}" = "1" ]; then \
	       printf '\n  Exit 0 ONLY because PV_VERIFY_ALLOW_EMPTY=1. Nothing was examined; nothing was proved.\n\n' >&2; \
	       exit 0; \
	     fi; \
	     printf '\n  db-verify FAILED: the corpus is empty, so V1-V10 returning zero proves nothing\n' >&2; \
	     printf '  and V11 returning 0 is correct rather than passing. Run `make seed` (T2.8) and\n' >&2; \
	     printf '  re-run. To acknowledge a deliberately pre-seed database, set PV_VERIFY_ALLOW_EMPTY=1.\n\n' >&2; \
	     exit 2 ;; \
	   *) \
	     printf '\n  db-verify: db/verify.sql produced no VERDICT line. The file or the connection is broken.\n\n' >&2; \
	     exit 3 ;; \
	 esac

db-reset:               ## (Phase 2) Drop and recreate the provenance_ci database.
	$(call unimplemented,2,T2.1 - destructive reset of the CI database only)

seed:                   ## Load the hero corpus and the 18000 decoys.
	$(PY) -m scripts.seed --profile $(SEED_PROFILE) $(SEED_ARGS)
	$(PY) -m tools.manifest_check db/seeds/MANIFEST.json

seed-perturb:           ## Reseed with the outcome-bearing rows removed or shifted.
# The leading `-` is REQUIRED. A perturbed seed fails its own verification by
# design: V11 returns 0 because the retraction fixtures are gone. Without the
# dash, make would stop here and the operator would read a deliberate failure as
# a broken target.
	-$(PY) -m scripts.seed --profile $(SEED_PROFILE) --perturb
	@printf '\n  Seed perturbed. Cases 3 and 5 are RESOLVED, the three retraction\n'
	@printf '  fixtures are deleted, and the 31 May termination window has moved\n'
	@printf '  sixty days. The seed exits NON-ZERO on purpose: V11 now returns 0.\n'
	@printf '  A suite that still passes is reading the seed rather than computing\n'
	@printf '  from it.  Restore with:  make seed-restore\n\n'

seed-restore:           ## Undo make seed-perturb, row for row.
	$(PY) -m scripts.seed --profile $(SEED_PROFILE) --restore

demo-reset:             ## Destructive reset to clean demo state.
	@test "$(CONFIRM)" = "yes" || { \
	  printf '\n  make demo-reset DROPs and recreates the database. Every seeded row,\n' >&2; \
	  printf '  every uploaded artifact row and every kernel commit goes with it.\n' >&2; \
	  printf '  Re-run as:  make demo-reset CONFIRM=yes\n' >&2; \
	  printf '  For a non-destructive rebuild use:  make seed SEED_ARGS=--reset\n\n' >&2; \
	  exit 1; }
	PROVENANCE_CONFIRM_DESTRUCTIVE=yes $(PY) -m scripts.seed \
	  --recreate-database $${PV_APP_DATABASE:-provenance}
	@D="$${PV_DB_MIGRATOR:-}"; \
	 if [ -z "$$D" ] && [ -f .env ]; then \
	   D=$$(grep '^PV_DB_MIGRATOR=' .env | cut -d= -f2- | tr -d '\r\n' || true); \
	 fi; \
	 test -n "$$D" || { printf '\n  demo-reset: no migrator URL. Set PV_DB_MIGRATOR, or put it in .env.\n\n' >&2; exit 1; }; \
	 COCKROACH_DATABASE_URL="$$D" $(PY) -m alembic -c alembic.ini upgrade head
	@printf '\n  Database recreated and migrated to head. Reseeding is deliberately a\n'
	@printf '  separate command so a reset that half-succeeds is visible:\n'
	@printf '      make seed\n'
	@printf '  BUDGET AN HOUR. The ANN index build over 18,035 rows was measured at\n'
	@printf '  52-55 minutes on this cluster, three times. ops/41_RUNBOOK.md section\n'
	@printf '  4.2 used to predict one to two minutes; that was wrong by ~30x, and\n'
	@printf '  S10 mandates this sequence near the deadline.\n\n'



embeddings-warm:        ## (Phase 6) Populate the embedding cache without touching the database.
	$(call unimplemented,6,T6.1 - Titan embeddings and db/seeds/vectors.parquet)

run-api:                ## (Phase 8) Control plane on :8080.
	@printf '\n  Serving as a FACTORY, not a module-level `app`. A module-level\n'
	@printf '  app resolves Settings at IMPORT, so any tool that merely imports\n'
	@printf '  main.py -- a linter walking the tree, a stray test collection --\n'
	@printf '  fails on an unset environment variable instead of doing its job.\n'
	@printf '  A factory moves that resolution to the moment the server starts,\n'
	@printf '  which is the only moment it means anything.\n\n'
	@printf '  On Windows: psycopg async refuses the proactor loop, and\n'
	@printf '  --loop asyncio SELECTS it -- uvicorn 0.40 resolves that flag to\n'
	@printf '  ProactorEventLoop on win32. The old recipe passed it and got\n'
	@printf '  db_ok=false against a cluster that was fine. scripts/run_api.py\n'
	@printf '  supplies the loop factory instead; a policy cannot, because\n'
	@printf '  uvicorn hands a factory to asyncio.run and ignores the policy.\n'
	@printf '  Startup still survives a refused pool and reports db_ok=false\n'
	@printf '  rather than crash-looping, so a 200 from /v1/version does NOT\n'
	@printf '  imply a database. Read the field, do not infer it.\n\n'
	@printf '  Settings reads the ENVIRONMENT and never parses .env itself\n'
	@printf '  (settings.py:331 -- a repository-root dotenv holding a live\n'
	@printf '  credential must not be parsed by every test that happens to\n'
	@printf '  run from the repo root). So the SHELL exports it here, per\n'
	@printf '  ops/41_RUNBOOK.md section 2.5. Without this line the server\n'
	@printf '  exits with 8 missing-field errors and input_value={}.\n\n'
	@printf '  GIT_SHA is READ FROM GIT, never written down. The status bar\n'
	@printf '  renders it on every screen, and a stamp that has to be kept\n'
	@printf '  in step by hand is a stamp that is eventually wrong -- which\n'
	@printf '  is worse than absent, because it looks verified.\n\n'
	set -a; [ -f .env ] && . ./.env; set +a; \
	  export GIT_SHA="$$(git rev-parse HEAD 2>/dev/null || echo unknown)"; \
	  $(PY) scripts/run_api.py --host 127.0.0.1 --port 8080

run-web:                ## (Phase 12) Next.js on :3000.
	@printf '\n  PV_API_BASE_URL unset means FIXTURE mode, behind a permanent\n'
	@printf '  banner. That is a supported mode, not a broken one -- but the\n'
	@printf '  recorded submission must run LIVE. Set PV_API_BASE_URL to the\n'
	@printf '  control plane and the banner goes away because the data is real.\n\n'
	cd apps/web && npm run dev

route-sweep:            ## (Phase 12) Load EVERY live route and report which break.
	@printf '\n  The web suite runs against fixtures, so it cannot see a route\n'
	@printf '  that dies on real data. Nine did: a contract that declared a\n'
	@printf '  decimal where the API sends a Money object, a Record where it\n'
	@printf '  sends null, a context every case was assumed to have. The\n'
	@printf '  fixtures agreed with the types because both were written from\n'
	@printf '  the same reading of the spec; only the server disagreed.\n\n'
	@printf '  Needs `make run-api` AND `make run-web` already running.\n'
	@printf '  Exit 2 means the sweep could not run, which is not the same\n'
	@printf '  claim as a broken route and must not be recorded as one.\n\n'
	$(PY) -m tools.route_sweep --warm

evals:                  ## (Phase 14) Score the section 2.2 capability claims; print a report.
	@printf '\n  Reads the live corpus. Nothing here embeds text: there is no Titan\n'
	@printf '  credential on this machine and the 18,035 stored vectors are\n'
	@printf '  amazon.titan-embed-text-v2:0 at 1024 dimensions, so every query\n'
	@printf '  vector is one already in the corpus. The measurements that need a\n'
	@printf '  fresh query vector report CANNOT RUN and name the credential.\n\n'
	@printf '  Exit 0 means no measured behaviour contradicted a claim -- which\n'
	@printf '  INCLUDES suites that could not run. Exit 1 is a real FAIL. Exit 2\n'
	@printf '  means the harness could not start and NOTHING was measured; it is\n'
	@printf '  a third code on purpose, because a gate reading 1 there would\n'
	@printf '  force a fallback for a capability nobody looked at (D-00-005).\n\n'
	@printf '  The session is READ ONLY and every statement is a SELECT. This\n'
	@printf '  harness writes to no table, canonical or otherwise.\n\n'
	$(PY) -m evals.runner

sabotage:               ## (Phase 14) Neuter each symbol in the matrix; assert its tests go red.
#
# A GREEN selection is a gate FAILURE, not a pass: the tests pass with the
# symbol neutered, so they do not depend on the behaviour they claim to test.
#
# CANNOT RUN is not a pass either, and the runner says so per entry. pytest
# exit 5 means the selection collected nothing; exit 0 WITH SKIPS means the
# discriminating tests may never have executed. The runner reported SURVIVED
# for retraction_filter on its first run and was wrong for exactly that
# reason -- the selection was `2 passed, 4 skipped` and the 4 skipped were the
# ones that touch the filter.
#
# --min-count is the append-only guard (72_DEFECT_PROTOCOL section 10.2
# detector 2): the matrix may grow, never shrink. Deleting an entry to make
# this target pass is the cheap fix both tools exist to catch.
	$(PY) -m tools.sabotage_guard --min-count 13
	$(PY) -m tools.sabotage_run

demo-rehearse:          ## (Phase 15) Can the dress rehearsal run right now, and where does it stop?
#
# ops/41_RUNBOOK.md section 8.1 lists twelve steps. This reports, per step,
# whether it could run -- it does NOT perform them. Step 1 alone is
# `demo-reset && seed && db-verify`, which destroys the demo corpus and takes
# about 55 minutes for the ANN index; a tool you run to find out whether you
# are ready must not cost an hour or consume the demo it is checking.
#
# BLOCKED names a capability that does not exist yet -- a build task, checked
# against the live UNBOUND register rather than asserted from memory, so this
# cannot keep claiming a step is blocked after the blocker is cleared.
# NOT READY means the capability exists but the world is not in the right
# state: a server to start, a corpus to reset. Minutes, not builds. Collapsing
# the two into 'failed' would send a reader to the wrong place.
	$(PY) -m tools.demo_readiness


test-submission:        ## (Phase 15) The Definition of Done, checked rather than recited.
#
# 05_RELIABILITY_EVAL_DEMO.md section 19. Five of its assertions require
# Cognito, S3, SES, EventBridge and CloudWatch -- the AWS stack PIVOT.md
# records as discarded by binding decision. Running them verbatim would report
# five permanent failures for a product this build deliberately does not ship;
# dropping them would report a green Definition of Done over a checklist nobody
# had reconciled. They are carried as SUPERSEDED, each naming the decision and
# the capability that replaced it, and the count is printed.
#
# CANNOT RUN blocks the exit code. MANUAL does not -- three assertions genuinely
# need a human (is the State Proof *understandable*?) and are never auto-passed.
#
# Needs `make run-api` and `make run-web` up for the route sweep.
	$(PY) -m tools.submission_check


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

gate-2:                 ## G2.1..G2.8 - schema migrations grants views and seed.
	$(call require_make_version)
	$(call require_gate_sh)
# THREE DEVIATIONS FROM THE BATTERY AS WRITTEN IN 23_PHASE_GATES.md section 8,
# each stated here rather than silently applied.
#
# 1. `cockroach sql --url` is replaced by `psql`. The cockroach CLI is not
#    installed on this machine; CockroachDB is wire-compatible and every
#    statement below is server-side SQL, so the client is not load-bearing.
#    Recorded the same way in ops/cluster-probe.txt.
#
# 2. G2.2 counts base tables and expects 26. The LIVE count is 27, because
#    Alembic creates `alembic_version` in `public` and it is a BASE TABLE.
#    It is migration bookkeeping, not a canonical table, and db/expected_tables.txt
#    correctly does not list it. The exclusion is EXPLICIT here so that the
#    number 26 keeps meaning "the canonical set" - filtering it silently would
#    let a genuine 27th table hide behind the same adjustment.
#
# 3. psql on Windows emits CRLF. `diff` against a LF file then reports all 26
#    lines as different while the content is identical. The `tr -d` is a
#    line-ending normaliser, NOT a content filter: it removes only \r, so a real
#    difference in any table name still fails. Without it G2.2 fails on arrival
#    for a reason that has nothing to do with the schema.
# WARNING, learned the hard way: G2.1's FIRST act is `downgrade base`, and the
# four cycles take well over ten minutes against a cloud cluster. Interrupting
# this target part-way leaves provenance_ci with NO SCHEMA, and every assertion
# after it then fails with "relation does not exist" -- which reads like a
# migration defect and is not one. If you kill it, restore with
#   COCKROACH_DATABASE_URL=$$PROVENANCE_TEST_DB_URL python -m alembic upgrade head
# before drawing any conclusion from a later assertion.
#
# Safe to interrupt ONLY because it runs against provenance_ci, which is
# disposable by design. It must never be pointed at `provenance`.
	@printf '\n=== G2.1  migrate from zero, down, and up again ===\n'
	$(GATE) G2.1 -- bash -c 'set -euo pipefail; \
	  export COCKROACH_DATABASE_URL="$$(grep "^PROVENANCE_TEST_DB_URL=" .env | cut -d= -f2- | tr -d "\r\n")"; \
	  $(PY) -m alembic -c alembic.ini downgrade base; \
	  $(PY) -m alembic -c alembic.ini upgrade head; \
	  $(PY) -m alembic -c alembic.ini downgrade base; \
	  $(PY) -m alembic -c alembic.ini upgrade head; \
	  $(PY) -m alembic -c alembic.ini current'
	@printf '\n=== G2.2  the canonical table set is complete and has nothing extra ===\n'
	$(GATE) G2.2 -- bash -c 'set -euo pipefail; \
	  D="$$(grep "^PROVENANCE_TEST_DB_URL=" .env | cut -d= -f2- | tr -d "\r\n")"; \
	  n=$$(psql -At -d "$$D" -c "SELECT count(*) FROM information_schema.tables WHERE table_schema=\"'"'"'public'"'"'\" AND table_type=\"'"'"'BASE TABLE'"'"'\" AND table_name <> \"'"'"'alembic_version'"'"'\";" | tr -d "\r"); \
	  echo "canonical base tables: $$n (alembic_version excluded, see note above)"; \
	  test "$$n" = "26"'
	$(GATE) G2.2b -- bash -c 'set -euo pipefail; \
	  D="$$(grep "^PROVENANCE_TEST_DB_URL=" .env | cut -d= -f2- | tr -d "\r\n")"; \
	  diff <(psql -At -d "$$D" -c "SELECT table_name FROM information_schema.tables WHERE table_schema=\"'"'"'public'"'"'\" AND table_type=\"'"'"'BASE TABLE'"'"'\" AND table_name <> \"'"'"'alembic_version'"'"'\" ORDER BY 1;" | tr -d "\r") \
	       <(sort db/expected_tables.txt | tr -d "\r"); \
	  echo "expected_tables.txt matches the live schema"'
	@printf '\n=== G2.3  the five agent views exist under the canon names ===\n'
	$(GATE) G2.3 -- bash -c 'set -euo pipefail; \
	  D="$$(grep "^PROVENANCE_TEST_DB_URL=" .env | cut -d= -f2- | tr -d "\r\n")"; \
	  got=$$(psql -At -d "$$D" -c "SELECT table_name FROM information_schema.views WHERE table_schema=\"'"'"'public'"'"'\" ORDER BY 1;" | tr -d "\r" | paste -sd,); \
	  echo "$$got"; \
	  test "$$got" = "agent_active_beliefs_v1,agent_belief_lineage_v1,agent_case_context_v1,agent_evidence_retrieval_v1,agent_open_obligations_v1"'
	@printf '\n=== G2.4  the ANN index exists and is prefixed by user_id ===\n'
	$(GATE) G2.4 -- bash -c 'set -euo pipefail; \
	  D="$$(grep "^PROVENANCE_TEST_DB_URL=" .env | cut -d= -f2- | tr -d "\r\n")"; \
	  psql -At -d "$$D" -c "SELECT column_name, seq_in_index FROM [SHOW INDEXES FROM evidence_items] WHERE index_name=\"'"'"'evidence_embedding_ann_idx'"'"'\" ORDER BY seq_in_index;" | tr -d "\r" | tee /dev/stderr | head -1 | grep -q "^user_id"'
	@printf '\n=== G2.6b  pv_agent_reader holds NO grant outside the five views ===\n'
	$(GATE) G2.6b -- bash -c 'set -euo pipefail; \
	  D="$$(grep "^PROVENANCE_TEST_DB_URL=" .env | cut -d= -f2- | tr -d "\r\n")"; \
	  n=$$(psql -At -d "$$D" -c "SELECT count(*) FROM information_schema.role_table_grants WHERE grantee=\"'"'"'pv_agent_reader'"'"'\" AND table_name NOT LIKE \"'"'"'agent\_%\_v1'"'"'\";" | tr -d "\r"); \
	  echo "non-view grants held by pv_agent_reader: $$n"; \
	  test "$$n" = "0"'
# ORDERING, corrected 2026-08-18. This ran db-verify BEFORE seed, which can never
# satisfy G2.5: on a freshly migrated database V11 is necessarily 0 and V1-V10
# return zero over an empty population, so the assertion would have reported
# success for a database with nothing in it. db-verify now runs TWICE, and the
# pre-seed run is the more interesting of the two - it proves the suite REFUSES
# an empty corpus rather than passing it.
#
# ASSERTED ON THE VERDICT TEXT, NOT ON AN EXIT CODE, and that is load-bearing.
# `make` reports its OWN status, not the recipe's: a recipe exiting 1 makes
# `make` print "Error 1" and exit 2. Testing $$? against 2 here would therefore
# accept FAIL_INVARIANT, FAIL_V11_UNDERSEEDED and VACUOUS_EMPTY_CORPUS alike -
# a green log for three outcomes that mean opposite things. Measured, not
# assumed: a FAIL_V11_UNDERSEEDED run produced "Error 1" and make exit 2.
	@printf '\n=== G2.5a  the verification suite REFUSES an empty corpus ===\n'
	$(GATE) G2.5a -- bash -c 'set -uo pipefail; \
	  out=$$($(MAKE) db-verify 2>&1 || true); \
	  printf "%s\n" "$$out" | grep -E "^VERDICT" | tee /dev/stderr | grep -q "^VERDICT VACUOUS_EMPTY_CORPUS" || { \
	    printf "\n  expected VERDICT VACUOUS_EMPTY_CORPUS on the pre-seed database.\n" >&2; \
	    printf "  A different verdict here means the database was not empty, so this\n" >&2; \
	    printf "  assertion proved nothing about the suite refusing an empty corpus.\n\n" >&2; \
	    exit 1; }'
	@printf '\n=== G2.6  seeding is idempotent and matches its manifest ===\n'
	$(MAKE) seed
	@printf '\n=== G2.5  every verification query, against the SEEDED corpus ===\n'
	$(GATE) G2.5 -- bash -c 'set -euo pipefail; \
	  out=$$($(MAKE) db-verify 2>&1); rc=0; \
	  printf "%s\n" "$$out"; \
	  printf "%s\n" "$$out" | grep -qE "^VERDICT PASS" ; \
	  printf "%s\n" "$$out" | grep -qE "^V1 0 .* V11 [3-9]"'
	@printf '\n=== G2.7 / G2.8  the schema refuses impossible money and ungrounded beliefs ===\n'
	$(GATE) G2.7-G2.8 -- $(PYTEST) -q services/control_plane/tests/db/test_kernel_required.py
	@printf '\nG-2 battery complete. Record every result in ops/gates/PHASE_02.md.\n'

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
