#!/usr/bin/env bash
#
# tools/gate.sh - capture one gate assertion's output as committed evidence.
#
#   usage: tools/gate.sh <ASSERTION-ID> [--] <command> [args...]
#   e.g.   tools/gate.sh G4.3 -- python -m tools.write_path_lint
#          tools/gate.sh G0.6 -- bash -c 'grep -c "^-- P" ops/cluster-probe.txt'
#
# Authority: quality/23_PHASE_GATES.md section 2.2 -
#
#     tools/gate.sh - usage: tools/gate.sh G4.3 -- <command...>
#     Runs the command, tees stdout+stderr to ops/gates/logs/<ID>.<sha8>.log,
#     scrubs it with tools/scrub.py (redacts URLs with credentials, JWTs, ARNs
#     containing account ids), and records the exit code in the log header.
#
# WHAT THIS SCRIPT GUARANTEES
#   1. The log exists whether the assertion passed or failed. A gate log that is
#      only written on success turns a FAIL into an absence, and section 3
#      rule 2 is explicit that a silently omitted assertion is a gate failure.
#   2. The log is scrubbed before it is written. ops/gates/ is committed and CI
#      runs `gitleaks detect --source ops/gates` on every push.
#   3. The header carries the assertion id, the git sha, an ISO-8601 UTC
#      timestamp, and the CHILD's exit code.
#   4. This script exits with the child's status, so `make gate-<N>` stops at
#      the first failing assertion instead of running the battery to a green
#      finish over a red result.
#
# =============================================================================
# THE PIPELINE-STATUS PROBLEM, WHICH IS THE WHOLE REASON THIS IS A SCRIPT
# =============================================================================
# In a POSIX shell a pipeline's exit status is the status of its LAST command.
# So the obvious implementation:
#
#     "$@" 2>&1 | python tools/scrub.py > "$log"
#     echo "exit=$?" >> "$log"          # <-- WRONG
#
# records the SCRUBBER's exit code. tools/scrub.py is a filter that succeeds on
# every input by design, so `$?` is 0 for every assertion that has ever been
# run. Every gate log reads exit=0. A failing assertion produces a log that says
# it passed, `make gate-<N>` continues to the next assertion, and the gate
# signs on evidence that is uniformly, silently wrong. That failure is invisible
# in exactly the way section 2.1 defines self-deception: "an assertion that
# would produce identical output whether the feature exists or not".
#
# `set -o pipefail` fixes the always-zero half but is still the wrong tool here.
# pipefail returns the RIGHTMOST non-zero status, which conflates two events
# that must never be conflated: an assertion that failed (the gate result) and a
# scrubber that crashed (a tooling fault whose log may be unscrubbed). Under
# pipefail a scrubber crash would be recorded as a failed assertion and the
# operator would go debug the wrong thing.
#
# So: pipefail is deliberately NOT set, and PIPESTATUS is read instead.
# PIPESTATUS is a bash array holding one status per pipeline element, left to
# right, valid only until the next command runs - which is why it is copied into
# a variable on the very next line. Element 0 is the child, element 1 is the
# scrubber, element 2 is tee. Each is handled on its own terms below.
# =============================================================================

set -u

readonly EXIT_USAGE=64        # EX_USAGE
readonly EXIT_TOOLING=70      # EX_SOFTWARE - a capture fault, never a gate result

usage() {
  cat >&2 <<'EOF'
usage: tools/gate.sh <ASSERTION-ID> [--] <command> [args...]

  ASSERTION-ID   the id from quality/23_PHASE_GATES.md, e.g. G4.3, G0.6, S5.
                 It becomes the log name: ops/gates/logs/<ID>.<sha8>.log

Environment (ops/gate-env.sh sets all of these; each has a fallback here so the
script also works when the prelude was not sourced):
  PV_REPO_ROOT   repository root                 default: git rev-parse --show-toplevel
  PV_GIT_SHA     commit under test               default: git rev-parse HEAD
  PV_GATE_LOG    log directory                   default: $PV_REPO_ROOT/ops/gates/logs
  PV_PYTHON      interpreter running the scrubber default: python, then python3

Exit status is the command's own, so `make gate-<N>` halts on the first red
assertion. The two exceptions are usage (64) and a capture fault (70); both are
reported loudly on stderr and neither can be mistaken for an assertion result.
EOF
  exit "$EXIT_USAGE"
}

[ "$#" -ge 1 ] || usage
ASSERTION_ID="$1"
shift
[ "${1:-}" = "--" ] && shift
[ "$#" -ge 1 ] || usage

# The id becomes a filename. Refuse anything that is not filename-safe rather
# than silently writing a log somewhere nobody will look for it.
case "$ASSERTION_ID" in
  '' | *[!A-Za-z0-9._-]*)
    printf 'tools/gate.sh: %s is not a usable assertion id (A-Z a-z 0-9 . _ - only)\n' \
      "$ASSERTION_ID" >&2
    exit "$EXIT_USAGE"
    ;;
esac

# --- environment, with fallbacks so the script stands alone -------------------

if [ -z "${PV_REPO_ROOT:-}" ]; then
  PV_REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi

if [ -z "${PV_GIT_SHA:-}" ]; then
  PV_GIT_SHA="$(git rev-parse HEAD 2>/dev/null || true)"
fi
if [ -n "${PV_GIT_SHA:-}" ]; then
  SHA8="${PV_GIT_SHA:0:8}"
else
  # No commit yet, or not a work tree. Say so in the header rather than writing
  # a plausible-looking zero sha: a gate log that names the wrong commit is
  # worse than one that admits it does not know.
  PV_GIT_SHA="UNKNOWN-NOT-A-GIT-WORKTREE"
  SHA8="nogitsha"
fi

# A gate result from a dirty tree is a claim about a commit that does not exist.
# It is recorded, not refused: section 4.1's "Environment of record" block asks
# the reviewer to state "fresh clone ... | reused working tree (state why)", and
# this line is the mechanical half of that answer.
if git rev-parse --is-inside-work-tree >/dev/null 2>&1 && [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  TREE_STATE="dirty"
else
  TREE_STATE="clean"
fi

PV_GATE_LOG="${PV_GATE_LOG:-$PV_REPO_ROOT/ops/gates/logs}"
mkdir -p "$PV_GATE_LOG" || {
  printf 'tools/gate.sh: cannot create log directory %s\n' "$PV_GATE_LOG" >&2
  exit "$EXIT_TOOLING"
}
LOG="$PV_GATE_LOG/$ASSERTION_ID.$SHA8.log"

SCRUB="$PV_REPO_ROOT/tools/scrub.py"
if [ ! -f "$SCRUB" ]; then
  printf 'tools/gate.sh: %s is missing.\n' "$SCRUB" >&2
  printf 'Refusing to capture: an unscrubbed log in ops/gates/ is a committed,\n' >&2
  printf 'public credential leak (quality/23_PHASE_GATES.md section 2.2).\n' >&2
  exit "$EXIT_TOOLING"
fi

if [ -n "${PV_PYTHON:-}" ]; then
  PY="$PV_PYTHON"
elif command -v python >/dev/null 2>&1; then
  PY="python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  printf 'tools/gate.sh: no python interpreter found; cannot run the scrubber.\n' >&2
  exit "$EXIT_TOOLING"
fi

# --- run ----------------------------------------------------------------------

BODY="$(mktemp "${TMPDIR:-/tmp}/pv-gate-XXXXXX")" || {
  printf 'tools/gate.sh: mktemp failed\n' >&2
  exit "$EXIT_TOOLING"
}
# shellcheck disable=SC2064  # expand BODY now, at trap-installation time
trap "rm -f '$BODY'" EXIT INT TERM

# The command line itself goes through the scrubber. It is the single most
# likely place for a credential to appear in a gate log, because a connection
# URL is an argument far more often than it is output.
CMD_SCRUB_STATUS=0
# The status of a command substitution is the status of the LAST command in the
# pipeline it contains - here, the scrubber. That is exactly the one to check.
CMD_DISPLAY="$(printf '%q ' "$@" | "$PY" "$SCRUB")" || CMD_SCRUB_STATUS=$?

# The scrubber's exit status on the command line is checked as carefully as its
# status inside the body pipeline below. Before D-00-005 it was not, so a
# scrubber that failed here and succeeded there produced a log with an empty
# `# cmd=` line and a normal `exit=0` header - a gate log that does not say
# what was run, with nothing flagging it. Take the same CAPTURE-FAILED path.
if [ "$CMD_SCRUB_STATUS" -ne 0 ] || [ -z "$CMD_DISPLAY" ]; then
  {
    printf '# gate=%s sha=%s utc=%s exit=CAPTURE-FAILED\n' \
      "$ASSERTION_ID" "$PV_GIT_SHA" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '# cmd=<UNAVAILABLE>\n'
    printf '# cmd_scrub_exit=%s\n' "$CMD_SCRUB_STATUS"
    printf '#\n'
    printf '# The assertion was NOT RUN. tools/scrub.py could not filter the command\n'
    printf '# line, so the command could not be shown to be redacted, and ops/gates/\n'
    printf '# is committed and public. Record this as NOT RUN in the phase ledger.\n'
  } >"$LOG"
  printf '\ntools/gate.sh: could not scrub the command line for %s (exit=%s).\n' \
    "$ASSERTION_ID" "$CMD_SCRUB_STATUS" >&2
  printf 'The assertion was not run: a log that cannot say what it ran is not evidence.\n' >&2
  exit "$EXIT_TOOLING"
fi

STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# The pipeline. Read the block at the top of this file before changing it.
#   [0] the assertion itself, stderr merged into stdout so a traceback is
#       evidence rather than something that scrolled past uncaptured
#   [1] the scrubber
#   [2] tee, so the operator watches the run live AND the body is captured -
#       and note the order: the terminal sees the SCRUBBED stream, so a
#       credential never reaches a scrollback or a CI console either
"$@" 2>&1 | "$PY" "$SCRUB" | tee "$BODY"
PIPE_STATUS=("${PIPESTATUS[@]}")   # must be the very next line

CHILD_STATUS="${PIPE_STATUS[0]}"
SCRUB_STATUS="${PIPE_STATUS[1]}"
TEE_STATUS="${PIPE_STATUS[2]}"

FINISHED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# --- capture faults ------------------------------------------------------------
# A scrubber or tee failure means the body may be partial or unfiltered. Writing
# it would be the leak this script exists to prevent, and reporting the child's
# status would be a gate result whose evidence was destroyed. So: no body, a
# header that says exactly what happened, and a tooling exit code that cannot be
# read as PASS or FAIL.
if [ "$SCRUB_STATUS" -ne 0 ] || [ "$TEE_STATUS" -ne 0 ]; then
  {
    printf '# gate=%s sha=%s utc=%s exit=CAPTURE-FAILED\n' \
      "$ASSERTION_ID" "$PV_GIT_SHA" "$FINISHED"
    printf '# cmd=%s\n' "$CMD_DISPLAY"
    printf '# child_exit=%s scrub_exit=%s tee_exit=%s\n' \
      "$CHILD_STATUS" "$SCRUB_STATUS" "$TEE_STATUS"
    printf '#\n'
    printf '# The captured body was DISCARDED. tools/scrub.py or tee failed, so the\n'
    printf '# output could not be shown to be redacted, and ops/gates/ is committed\n'
    printf '# and public. This log is NOT an assertion result: re-run the assertion\n'
    printf '# after fixing the capture. Record it as NOT RUN in the phase ledger.\n'
  } >"$LOG"
  printf '\ntools/gate.sh: CAPTURE FAILED for %s (scrub=%s tee=%s). Body discarded.\n' \
    "$ASSERTION_ID" "$SCRUB_STATUS" "$TEE_STATUS" >&2
  printf 'The assertion itself exited %s, but that result has no evidence behind it.\n' \
    "$CHILD_STATUS" >&2
  exit "$EXIT_TOOLING"
fi

# --- write the log -------------------------------------------------------------
# Line 1 is the header section 2.2 asks for: assertion id, git sha, ISO-8601
# timestamp, exit code. It is one line so that
#   head -1 ops/gates/logs/*.log
# reads the whole battery at a glance, and so that
#   grep -L 'exit=0' ops/gates/logs/*.log
# lists exactly the assertions that failed.
{
  printf '# gate=%s sha=%s utc=%s exit=%s\n' \
    "$ASSERTION_ID" "$PV_GIT_SHA" "$FINISHED" "$CHILD_STATUS"
  printf '# cmd=%s\n' "$CMD_DISPLAY"
  printf '# started=%s finished=%s tree=%s scrubbed-by=tools/scrub.py\n' \
    "$STARTED" "$FINISHED" "$TREE_STATE"
  printf '#\n'
  cat "$BODY"
} >"$LOG" || {
  printf 'tools/gate.sh: could not write %s\n' "$LOG" >&2
  exit "$EXIT_TOOLING"
}

printf '\n[gate] %s exit=%s -> %s\n' "$ASSERTION_ID" "$CHILD_STATUS" "$LOG" >&2

# Exit with the CHILD's status. Not the scrubber's, not tee's, not zero.
exit "$CHILD_STATUS"
