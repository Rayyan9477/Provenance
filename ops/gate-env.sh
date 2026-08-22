# shellcheck shell=bash
#
# Provenance - the gate environment prelude.
#
# Authority: quality/23_PHASE_GATES.md section 2.2, reproduced below verbatim.
# "Every battery is run after sourcing a single environment file. Credentials
#  are never typed into a command and never appear in a gate log."
#
# SOURCE this file; do not execute it. The exports must land in the shell that
# then runs `make gate-<N>`:
#
#     source ops/gate-env.sh && make gate-4
#
# It is committed and it contains no secret. That is not an accident of what has
# been written here so far - it is the design. Every credential is resolved at
# call time by `asm-exec` into a child process environment, so there is no file
# anywhere in this repository that a leak could come out of.
#
# =============================================================================

# ops/gate-env.sh  (committed; contains no secrets)
export PV_REPO_ROOT="$(git rev-parse --show-toplevel)"
export PV_GIT_SHA="$(git rev-parse HEAD)"
export PV_REGION=us-east-1
export PV_API="${PV_API:-http://localhost:8080}"
export PV_WEB="${PV_WEB:-http://localhost:3000}"
export PV_GATE_LOG="${PV_REPO_ROOT}/ops/gates/logs"
mkdir -p "$PV_GATE_LOG"

# Connection URLs are resolved at call time from AWS Secrets Manager and never
# printed. asm-exec substitutes {{resolve:secretsmanager:...}} into the child
# process environment only; the value never enters a shell history or a log.
#   asm-exec --env PV_DB_MIGRATOR='{{resolve:secretsmanager:provenance/db:SecretString:migrator_url}}' -- <cmd>
# Roles, in ascending privilege over canonical tables:
#   pv_agent_reader        SELECT on agent_*_v1 views only
#   pv_app_reader_writer   non-canonical writes + reads
#   pv_kernel_writer       the only role that writes canonical tables
#   pv_migrator            DDL only, never used by runtime

# -----------------------------------------------------------------------------
# Two notes that section 2.2 does not carry, and that a reader of this file
# needs. Neither adds a variable.
#
# 1. There is a fifth role. CANONICAL_DECISIONS.md ("Names and counts", and the
#    `pv_ops_reader` row of the hero commit canon) creates `pv_ops_reader` in
#    migration 0008: strictly read-only over the five agent_*_v1 views and the
#    eleven operational tables, an operator and CI credential only, never an App
#    Runner pool. It is absent from the section 2.2 ladder above because that
#    ladder is ordered by privilege over *canonical tables* and pv_ops_reader
#    has none. The `provenance/db` secret carries five keys accordingly:
#    migrator_url, app_url, kernel_url, agent_url, ops_reader_url.
#
# 2. PV_API and PV_WEB default to localhost, and from G-13 onward that default
#    is a trap. quality/23_PHASE_GATES.md section 23.11: "Any assertion in a
#    post-13 report whose command names localhost is marked NOT RUN." Set both
#    to the deployed URLs before running gate-13 and later batteries; the log
#    header written by tools/gate.sh does not record them, so the gate report
#    has to say which stack the output came from.
# -----------------------------------------------------------------------------
