"""Tests for `tools/scrub.py`.

Written before `tools/scrub.py`, per `EXECUTION/70_TASK_PLAN.md` T0.3
("Tests first: tools/tests/test_scrub.py").

The file has two halves and they are equally load-bearing.

**Leak cases.** One per rule. Each is a line that would put a live credential
into `ops/gates/logs/`, which is a committed, public, gitleaks-scanned directory
(`23_PHASE_GATES.md` section 2.2). Delete the rule and the corresponding test
fails, so no rule in `scrub.py` is decoration.

**False-positive cases.** A scrubber that eats real evidence is worse than no
scrubber, because the gate log is the evidence and `23_PHASE_GATES.md` section 3
forbids reporting a phase complete without it. `[REDACTED-ACCOUNT]` where the
corpus count `18035` used to be turns `G6.1` into an unreadable line, and
`[REDACTED-ACCOUNT]` where a case id used to be turns `G4.2` into a claim nobody
can check. Every value below is one this build actually prints:

  * the cluster id `4023638b-52be-42bd-9677-d3611c613477` (`ops/decisions/CLUSTER.md`)
  * the UUID `00000000-0000-4000-8000-000000000001` whose last group is twelve
    digits - the exact shape that a naive `\\d{12}` account rule destroys
  * the corpus counts `18035` and `16035` (`CANONICAL_DECISIONS.md`, hero commit canon)
  * the embedding dimension `1024` (`amazon.titan-embed-text-v2:0`)
  * a 40-character git sha, which an over-eager key rule would eat
  * a dotted `PV_SABOTAGE` symbol path, which a generic three-segment
    base64url JWT rule would eat
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools.scrub import RULES, scrub_text

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRUB_PY = REPO_ROOT / "tools" / "scrub.py"

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Leak cases - one per rule. Each names the rule it exists to protect.
# ---------------------------------------------------------------------------

# (rule_name, line that leaks, substring that must NOT survive)
LEAKS: list[tuple[str, str, str]] = [
    (
        "url-credential",
        "psql: could not connect to "
        "postgresql://pv_kernel_writer:s3cr3t-Pa55@rayyandb-32190.j77."
        "aws-us-east-1.cockroachlabs.cloud:26257/provenance?sslmode=verify-full",
        "s3cr3t-Pa55",
    ),
    (
        "url-credential",
        # The acceptance command in EXECUTION/70_TASK_PLAN.md T0.3, verbatim:
        # the log body must contain no ":p@" substring.
        "postgresql://u:p@h/d",
        ":p@",
    ),
    (
        "bearer-token",
        'curl -sS "$PV_API/v1/me" -H "Authorization: Bearer '
        'AbCdEf0123456789ghIjKlMnOpQrStUvWxYz"',
        "AbCdEf0123456789ghIjKlMnOpQrStUvWxYz",
    ),
    (
        "jwt",
        "cognito id_token=eyJraWQiOiJhYmMiLCJhbGciOiJSUzI1NiJ9."
        "eyJzdWIiOiJhbGV4LXJpdmVyYSIsImF1ZCI6InByb3ZlbmFuY2Utd2ViIn0."
        "c2lnbmF0dXJlLWJ5dGVzLWhlcmU",
        "eyJzdWIiOiJhbGV4LXJpdmVyYSIsImF1ZCI6InByb3ZlbmFuY2Utd2ViIn0",
    ),
    (
        "arn-account-id",
        "MCP_AUTH_SECRET_ARN=arn:aws:secretsmanager:us-east-1:210987654321:"
        "secret:provenance/db-AbCdEf",
        "210987654321",
    ),
    (
        "bare-account-id",
        '{"UserId": "AIDAEXAMPLE", "Account": "210987654321"}',
        "210987654321",
    ),
    (
        "aws-access-key-id",
        "aws_access_key_id = AKIAIOSFODNN7EXAMPLE",
        "AKIAIOSFODNN7EXAMPLE",
    ),
    (
        "sql-password-literal",
        "CREATE USER pv_kernel_writer WITH PASSWORD 'Kj8sQ2mNvR4tL9xW';",
        "Kj8sQ2mNvR4tL9xW",
    ),
    (
        "password-assignment",
        "PGPASSWORD=Kj8sQ2mNvR4tL9xW psql -h rayyandb-32190.j77.aws-us-east-1."
        'cockroachlabs.cloud -p 26257 -U pv_migrator -d provenance -c "SELECT 1"',
        "Kj8sQ2mNvR4tL9xW",
    ),
    (
        "aws-secret-credential",
        "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    ),
    (
        "named-secret-assignment",
        "PROVENANCE_CAPABILITY_HMAC_KEY=9f8e7d6c5b4a39281706f5e4d3c2b1a0",
        "9f8e7d6c5b4a39281706f5e4d3c2b1a0",
    ),
    (
        # The Gemini pivot put an AI Studio key in `.env`, and the Developer
        # API takes it as a QUERY PARAMETER rather than a header. So every
        # google-genai error that renders the failing request URL -- which is
        # most of them -- carries the live key in the message, and
        # `ops/probes/gemini_probe.py` writes exactly those messages into
        # `ops/gemini-probe.txt`, a committed file in a repository that goes
        # public at submission. No pre-existing rule matched: `url-credential`
        # requires `user:secret@`, and a query string has neither.
        "google-api-key",
        "google.genai.errors.ClientError: 400 INVALID_ARGUMENT calling "
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.7-flash:generateContent?key=AIzaSyC7QwErTyUiOpAsDfGhJkLzXcVbNm123",
        "AIzaSyC7QwErTyUiOpAsDfGhJkLzXcVbNm123",
    ),
    (
        "google-api-key",
        # Bare, with no keyword in front of it -- the shape a traceback frame
        # or a repr of the client config produces.
        "Client(api_key='AIzaSyC7QwErTyUiOpAsDfGhJkLzXcVbNm123', http_options=None)",
        "AIzaSyC7QwErTyUiOpAsDfGhJkLzXcVbNm123",
    ),
    (
        # `_API_KEY` was NOT in the suffix list: it ran
        # `HMAC_KEY|SIGNING_KEY|PRIVATE_KEY|SECRET_KEY|_SECRET|_TOKEN|_PASSWORD`.
        # The keyword rule is kept alongside `google-api-key` because it covers
        # a non-Google key that has no distinguishing prefix to anchor on.
        "named-secret-assignment",
        "GOOGLE_API_KEY=sk-notgoogle-0123456789abcdefghijklmnop",
        "sk-notgoogle-0123456789abcdefghijklmnop",
    ),
]


@pytest.mark.parametrize(("rule_name", "line", "secret"), LEAKS, ids=[c[1][:28] for c in LEAKS])
def test_rule_redacts_the_secret_it_names(rule_name: str, line: str, secret: str) -> None:
    """Without the named rule this exact line reaches a committed gate log."""
    assert rule_name in {
        rule.name for rule in RULES
    }, f"{rule_name} is not a rule in tools/scrub.py; the test and the module disagree"
    scrubbed = scrub_text(line)
    assert secret not in scrubbed, f"{rule_name} did not redact {secret!r}"
    assert scrubbed != line, f"{rule_name} left the line untouched"
    assert "[REDACTED" in scrubbed, "a redaction must be visible, not silent deletion"


def test_every_rule_has_at_least_one_leak_case() -> None:
    """A rule with no failing-without-it test is a rule nobody has checked.

    This is the `L-VAC` question (`72_DEFECT_PROTOCOL.md` section 3) asked of the
    scrubber itself: which rule could be deleted with every test still green?
    """
    covered = {rule_name for rule_name, _, _ in LEAKS}
    declared = {rule.name for rule in RULES}
    assert declared == covered, (
        f"rules without a leak case: {sorted(declared - covered)}; "
        f"leak cases naming no rule: {sorted(covered - declared)}"
    )


# ---------------------------------------------------------------------------
# False-positive cases - every one is a value this build really prints.
# ---------------------------------------------------------------------------

NOT_SECRETS: list[tuple[str, str]] = [
    (
        "cluster id",
        "cluster rayyandb id 4023638b-52be-42bd-9677-d3611c613477 plan BASIC region us-east-1",
    ),
    (
        "uuid whose last group is 12 digits",
        "EXPLAIN SELECT id FROM _pv_probe WHERE k = '00000000-0000-4000-8000-000000000001';",
    ),
    (
        "corpus row counts",
        "amazon.titan-embed-text-v2:0/1024/v1,18035  (user-scoped 16035)",
    ),
    (
        "vector dimension",
        "PB-5 titan embedding dimensions returned: 1024, l2 norm 1.0000000",
    ),
    (
        "git sha",
        "PV_GIT_SHA=c2a0c05f1e2d3b4a5968770e1c2d3b4a59687701 schema_revision=0008",
    ),
    (
        "dotted sabotage symbol",
        "PV_SABOTAGE=retrieval.predicates.retraction_filter pytest -q; echo exit=$?",
    ),
    (
        "dotted module path long enough to look like a JWT",
        "services.control_plane.app.memory_kernel.preflight.assert_grounded",
    ),
    (
        "url with no credential",
        'curl -sS -o /dev/null -w "%{http_code}" https://github.com/Rayyan9477/Provenance',
    ),
    (
        "url with a port but no userinfo",
        "PV_API=http://localhost:8080 PV_WEB=http://localhost:3000",
    ),
    (
        "unexpanded shell placeholder in an Authorization header",
        'curl -sS "$PV_API/v1/cases/x" -H "Authorization: Bearer $PV_TOKEN"',
    ),
    (
        "money and dates",
        "outstanding_amount=1800.0000 due_at=2026-06-15T00:00:00Z monetary_exposure=186.00",
    ),
    (
        "epoch milliseconds - 13 digits, not an account id",
        "started_at_ms=1755432000000 duration_ms=412",
    ),
    (
        "an arn with no account id",
        "arn:aws:s3:::provenance-artifacts-us-east-1/hero/E3_isp_invoice.pdf",
    ),
    (
        "unexpanded shell placeholder in a password assignment",
        "PGPASSWORD=$PV_DB_PASSWORD psql -h localhost -p 26257 -U pv_migrator -d provenance",
    ),
    (
        "a secretsmanager resolve template, which is a reference and not a value",
        "asm-exec --env U='{{resolve:secretsmanager:provenance/db:SecretString:migrator_url}}'",
    ),
    (
        "a *_SECRET_ARN names a reference; settings.py records that an ARN is safe to log",
        "COGNITO_WORKER_CLIENT_SECRET_ARN=arn:aws:secretsmanager:us-east-1:"
        "[REDACTED-ACCOUNT]:secret:provenance/worker",
    ),
    (
        "a Gemini model id, which the probe transcript exists to print",
        "PASS  gemini-3.7-flash  reply='ok' tokens=12  (Tier R canon)",
    ),
    (
        "an unexpanded placeholder in an API key assignment",
        "GOOGLE_API_KEY=$GOOGLE_API_KEY python ops/probes/gemini_probe.py",
    ),
    (
        "the generativelanguage endpoint with no key parameter",
        "endpoint=https://generativelanguage.googleapis.com/v1beta/models  sdk=google-genai",
    ),
]


@pytest.mark.parametrize(("what", "line"), NOT_SECRETS, ids=[c[0] for c in NOT_SECRETS])
def test_non_secret_evidence_passes_through_byte_identical(what: str, line: str) -> None:
    """A scrubber that eats evidence destroys the thing the gate log is for."""
    assert scrub_text(line) == line, f"{what} was mangled by the scrubber"


def test_a_clean_multi_line_transcript_is_unchanged() -> None:
    transcript = (
        "-- P2 vector index, cosine, prefix column\n"
        "CREATE VECTOR INDEX pv_probe_a ON _pv_probe (k, v vector_cosine_ops);\n"
        "CREATE INDEX\n"
        "\n"
        "V1 0  V2 0  V3 0  V4 0  V5 0  V6 0  V7 0  V8 0  V9 0  V10 0  V11 3\n"
    )
    assert scrub_text(transcript) == transcript


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def test_redaction_is_idempotent() -> None:
    """Re-scrubbing a scrubbed log must not degrade it further.

    `72_DEFECT_PROTOCOL.md` section 5.1 pipes pasted reproductions through this
    module before they are committed, and those reproductions are frequently
    copied out of an already-scrubbed gate log.
    """
    line = LEAKS[0][1]
    once = scrub_text(line)
    assert scrub_text(once) == once


def test_line_count_is_preserved() -> None:
    """Redaction replaces; it never drops or joins lines.

    A gate assertion is often a line count (`grep -c "^-- P"` == 11 at `G0.6`),
    so a scrubber that silently removed a line would change a gate result.
    """
    text = "\n".join(line for _, line, _ in LEAKS) + "\n"
    assert scrub_text(text).count("\n") == text.count("\n")


def test_an_arn_keeps_its_service_and_region_after_the_account_is_redacted() -> None:
    """Redaction must cost the secret and not the diagnostic value around it."""
    scrubbed = scrub_text("arn:aws:apprunner:us-east-1:210987654321:service/provenance/abc")
    assert scrubbed.startswith("arn:aws:apprunner:us-east-1:")
    assert scrubbed.endswith(":service/provenance/abc")
    assert "210987654321" not in scrubbed


def test_the_sql_role_name_does_not_survive_a_connection_url() -> None:
    """Both halves of the userinfo go.

    Some CockroachDB Cloud connection strings carry the token in the *user*
    field rather than the password field, so redacting only the password leaks
    on exactly the shape that matters.
    """
    scrubbed = scrub_text("postgresql://pv_kernel_writer:tok@host:26257/provenance")
    assert "pv_kernel_writer" not in scrubbed
    assert "tok" not in scrubbed
    assert "host:26257/provenance" in scrubbed


# ---------------------------------------------------------------------------
# The command-line contract that tools/gate.sh depends on
# ---------------------------------------------------------------------------


def test_cli_filters_stdin_to_stdout() -> None:
    """`tools/gate.sh` pipes the child's merged output through this CLI."""
    leaking = "postgresql://u:p@h/d\nV11 3\n"
    result = subprocess.run(
        [sys.executable, str(SCRUB_PY)],
        input=leaking,
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert ":p@" not in result.stdout
    assert "V11 3" in result.stdout


def test_cli_exits_zero_on_empty_input() -> None:
    """An assertion that legitimately prints nothing must not fail the capture."""
    result = subprocess.run(
        [sys.executable, str(SCRUB_PY)],
        input="",
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
