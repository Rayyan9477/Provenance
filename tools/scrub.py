#!/usr/bin/env python3
"""Redact credentials from gate evidence before it is committed.

Authority: `quality/23_PHASE_GATES.md` section 2.2 -

    tools/gate.sh - usage: tools/gate.sh G4.3 -- <command...>
    Runs the command, tees stdout+stderr to ops/gates/logs/<ID>.<sha8>.log,
    scrubs it with tools/scrub.py (redacts URLs with credentials, JWTs, ARNs
    containing account ids), and records the exit code in the log header.

and, immediately after it: "**Gate logs are committed.** They are scrubbed
first, and CI runs `gitleaks detect --source ops/gates` on every push. A gate log
that fails the scan blocks the merge; the secret it exposed is rotated before
anything else happens."

`EXECUTION/72_DEFECT_PROTOCOL.md` section 5.1 puts the same filter in front of
every defect reproduction, for the same reason: `ops/defects/` is committed too.

USAGE
-----
    <command> 2>&1 | python tools/scrub.py            # filter, the gate.sh path
    python tools/scrub.py FILE [FILE...]              # filter named files to stdout
    python tools/scrub.py --rules                     # print the rule table

Exit status is 0 on success and 2 on an I/O error. The filter never fails on
content: there is no such thing as input this module refuses, because refusing
would drop a gate assertion's output on the floor.

DESIGN NOTES, BOTH DIRECTIONS
-----------------------------
A scrubber has two failure modes and they are not symmetric in *kind*, only in
cost.

*Under-redaction* puts a live database credential in a public repository. The
recovery is a rotation, and `23_PHASE_GATES.md` section 6 names it as the one
thing in Phase 0 that cannot be undone.

*Over-redaction* destroys the evidence the log exists to carry. `23_PHASE_GATES.md`
section 3 is the rule the whole pack rests on - "No phase may be reported
complete without pasted command output" - and a log reading
`[REDACTED-ACCOUNT]` where `18035` used to be is not pasted command output. The
false-positive tests in `tools/tests/test_scrub.py` are therefore not politeness;
each one is a value this build prints into a gate log on purpose.

So every rule below is anchored on a token shape that is *only* a secret. The
one that needed real care is the bare twelve-digit AWS account id, because
`\\d{12}` also matches the last group of a UUID - and `00000000-0000-4000-8000-
000000000001` is a seed id this build prints. The lookarounds solve it: a run of
twelve digits is an account id only when nothing word-like, dotted or hyphenated
touches either end.

Two limits, stated rather than hidden:

1. The filter is **line-oriented**. A secret split across a newline is not
   matched. Nothing this build emits does that, and streaming line by line is
   what lets a long gate assertion show progress instead of buffering to the
   end.
2. The rules match *shapes*, not *values*. A credential that looks like ordinary
   prose - a passphrase pasted bare on its own line - passes through. That is
   why `gitleaks detect --source ops/gates` runs in CI as well (`G0.3`, `S8`):
   this module is the first filter, not the only one.
"""

from __future__ import annotations

import argparse
import contextlib
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

__all__ = ["RULES", "Rule", "scrub_line", "scrub_text"]


@dataclass(frozen=True)
class Rule:
    """One redaction. `name` is the id the tests parametrize over."""

    name: str
    pattern: re.Pattern[str]
    replacement: str
    why: str


# `tools/gate.sh` renders the child command line with `printf '%q '` before
# piping it through this module, so on the `# cmd=` header line every space is
# `\ ` and every quote is `\'`. A rule anchored on a plain `\s` separator
# therefore redacts a credential in the command's OUTPUT and misses the byte-
# identical credential in the command's ARGUMENTS - which is where a DSN or a
# PGPASSWORD appears far more often. `_SEP` accepts both spellings. (D-00-005.)
_SEP = r"[\s\\]*"

# Unquoted secret values stop at whitespace, at a `%q` escape, and at the shell
# punctuation that ends an argument. Stopping at `\\` is what keeps the
# redaction on a `# cmd=` line from swallowing the following `\;` or `\"`.
_VALUE = r"'[^']*'|\"[^\"]*\"|[^\s\\&;,)]+"

# ---------------------------------------------------------------------------
# The rules, in application order. Order matters once: `arn-account-id` runs
# before `bare-account-id` so that an account id inside an ARN is replaced by
# the marker that keeps the ARN readable.
# ---------------------------------------------------------------------------

RULES: tuple[Rule, ...] = (
    Rule(
        name="url-credential",
        # scheme://user:secret@host - the CockroachDB Cloud connection string
        # shape, and the one gitleaks flags. `user` and `secret` both stop at
        # `/ ? # @ :` so that `http://localhost:8080/a@b` is not a match: the
        # userinfo of a URL cannot contain a slash.
        #
        # Deliberately requires the `:secret` half. `postgresql://user@host/db`
        # carries no credential, and redacting it would cost the role name that
        # `G3.1` reads a pool's identity from.
        pattern=re.compile(
            r"(?P<scheme>\b[A-Za-z][A-Za-z0-9+.\-]*://)"
            r"(?P<user>[^\s/?#@:]+)"
            r":"
            r"(?P<secret>[^\s/?#@]*)"
            r"@"
        ),
        replacement=r"\g<scheme>[REDACTED-USER]:[REDACTED-SECRET]@",
        why=(
            "A connection URL with credentials is the leak this build is most likely to "
            "produce, because every psql and cockroach invocation takes one on the command "
            "line. Both halves of the userinfo are redacted: some Cloud connection strings "
            "carry the token in the user field."
        ),
    ),
    Rule(
        name="bearer-token",
        # `Bearer <token>` in an Authorization header. The character class
        # starts at `[A-Za-z0-9]`, so an unexpanded shell placeholder -
        # `Bearer $PV_TOKEN`, which is how every curl in 23_PHASE_GATES.md is
        # written - does not match and survives intact.
        pattern=re.compile(r"(?i)\b(?P<kw>Bearer)\s+(?P<tok>[A-Za-z0-9\-._~+/]{8,}={0,2})"),
        replacement=r"\g<kw> [REDACTED-BEARER-TOKEN]",
        why=(
            "G8.2-G8.7, G11.4, G12.x and S3 all curl with a real Cognito access token in "
            "the header, and the command line is echoed into the log header."
        ),
    ),
    Rule(
        name="jwt",
        # A JWT's header segment is base64url of a JSON object, so it always
        # begins `eyJ`. Anchoring on that is what makes this rule safe: the
        # obvious alternative - three base64url segments separated by dots -
        # also matches `services.control_plane.app.memory_kernel`, and eating a
        # PV_SABOTAGE symbol out of a G4.9 log would be an over-redaction that
        # destroys the assertion.
        pattern=re.compile(r"\beyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}"),
        replacement="[REDACTED-JWT]",
        why=(
            "Cognito id and access tokens reach the log whenever a token is decoded or "
            "echoed; the payload segment carries the subject and the client id."
        ),
    ),
    Rule(
        name="arn-account-id",
        # Only the account field is replaced. The service and region are the
        # diagnostic content of an ARN - G13.6 asserts which secret ARNs are
        # bound to App Runner - and redacting the whole ARN would make that
        # assertion unreadable.
        pattern=re.compile(
            r"(?P<head>\barn:aws[a-z0-9\-]*:[a-z0-9\-]*:[a-z0-9\-]*:)(?P<account>\d{12})(?=[:/])"
        ),
        replacement=r"\g<head>[REDACTED-ACCOUNT]",
        why=(
            "Every ARN this build prints - Secrets Manager, App Runner, EventBridge, SQS, "
            "AgentCore - carries the AWS account id in its fifth field."
        ),
    ),
    Rule(
        name="bare-account-id",
        # Twelve digits touching nothing word-like, dotted or hyphenated.
        #
        # The lookarounds are the whole rule. Without them:
        #   00000000-0000-4000-8000-000000000001  -> the seed id is destroyed
        #   1755432000000                         -> 13-digit epoch ms is mangled
        #   123456789012.50                       -> a money value is mangled
        # With them, `"Account": "210987654321"` from `aws sts get-caller-identity`
        # still matches, which is the case this rule exists for.
        pattern=re.compile(r"(?<![0-9A-Za-z_.\-])(?P<account>\d{12})(?![0-9A-Za-z_.\-])"),
        replacement="[REDACTED-ACCOUNT]",
        why=(
            "An AWS account id is not a credential on its own, but it is the identifier an "
            "attacker needs to target one, and gitleaks treats a public account id as a "
            "finding."
        ),
    ),
    Rule(
        name="aws-access-key-id",
        # AKIA = long-lived access key id, ASIA = STS temporary. Twenty
        # characters, uppercase and digits only, so a 40-character lowercase git
        # sha - which every gate log header carries - cannot match.
        pattern=re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        replacement="[REDACTED-AWS-KEY-ID]",
        why=(
            "An access key id appears whenever an AWS CLI error, a botocore traceback or a "
            "credentials file is echoed into a transcript, and it names the identity whose "
            "secret half may be one line away."
        ),
    ),
    Rule(
        name="google-api-key",
        # `AIza` + at least 30 characters of base64url. A published Google key is
        # 39 characters, but the quantifier is a FLOOR rather than that exact
        # width: pinning it to 39 means a key of any other length passes through
        # in full, and under-redaction is the failure mode that cannot be undone.
        # The prefix is what makes this
        # rule safe to apply with no keyword in front of it: a 40-character git
        # sha is lowercase hex and cannot begin `AIza`, and the model ids,
        # dimensions and row counts the probe transcript prints are far shorter
        # than 39 characters.
        #
        # It has to match bare, because the Gemini Developer API takes the key
        # as a QUERY PARAMETER rather than an Authorization header. Every
        # google-genai error that renders its failing request URL therefore
        # carries the live key inside the message body, where `url-credential`
        # cannot see it -- that rule requires a `user:secret@` userinfo, and a
        # query string has no userinfo at all. `ops/probes/gemini_probe.py`
        # writes those messages to `ops/gemini-probe.txt`, which is committed.
        pattern=re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),
        replacement="[REDACTED-GOOGLE-API-KEY]",
        why=(
            "The AI Studio key is the only credential the Gemini path uses -- there is no "
            "service account, no ADC and no IAM binding -- so it is the single value whose "
            "exposure hands over the whole model budget. It reaches a transcript through "
            "the query string of a failed request, which no keyword-anchored rule matches."
        ),
    ),
    # ----------------------------------------------------------------------
    # The five shapes below were missing until D-00-005. `.gitleaks.toml`
    # lines 46-50 enumerate the six shapes "this project can actually leak";
    # this module covered three of them, and `ops/probes/phase0-probe.ps1`
    # lines 99-104 already implemented the rest - so the repository had two
    # scrubbers and the weaker one guarded the committed artefact.
    #
    # Every value class below stops at a placeholder (`$VAR`, `<unset>`,
    # `{{resolve:...}}`) via `(?![\s$<{])`, because the design pack writes its
    # commands with unexpanded shell variables on purpose and redacting those
    # would destroy the command a reviewer has to re-run.
    # ----------------------------------------------------------------------
    Rule(
        name="sql-password-literal",
        # CREATE USER pv_kernel_writer WITH PASSWORD 'xxx';
        # ALTER USER ... WITH PASSWORD 'xxx';
        pattern=re.compile(r"(?i)\bPASSWORD" + _SEP + r"'(?P<secret>[^'\\]*)\\?'"),
        replacement="PASSWORD '[REDACTED-SECRET]'",
        why=(
            "T0.5 and Phase 11 both create SQL roles, and CREATE/ALTER USER ... WITH "
            "PASSWORD is echoed by the cockroach CLI into exactly the transcripts G0.6 "
            "and G11.x read. The password is the whole credential."
        ),
    ),
    Rule(
        name="password-assignment",
        # PGPASSWORD=xxx psql ... / password=xxx in a DSN query string /
        # --password=xxx on a command line.
        pattern=re.compile(
            r"(?i)\b(?P<kw>PGPASSWORD|PASSWORD|PASSWD|PWD)"
            + _SEP
            + r"="
            + _SEP
            + r"(?![\s$<{])(?P<secret>"
            + _VALUE
            + r")"
        ),
        replacement=r"\g<kw>=[REDACTED-SECRET]",
        why=(
            "`PGPASSWORD=... psql` is the form the runbook uses when a DSN would put the "
            "credential in the URL, and `password=` is how it arrives in a libpq keyword "
            "string. Neither shape contains `://`, so `url-credential` never sees it."
        ),
    ),
    Rule(
        name="aws-secret-credential",
        pattern=re.compile(
            r"(?i)\b(?P<kw>aws_secret_access_key|aws_session_token|aws_security_token)"
            + _SEP
            + r"[:=]"
            + _SEP
            + r"(?![\s$<{])(?P<secret>"
            + _VALUE
            + r")"
        ),
        replacement=r"\g<kw>=[REDACTED-SECRET]",
        why=(
            "The secret half of the pair `aws-access-key-id` already redacts. A botocore "
            "traceback, `aws configure list`, or a pasted ~/.aws/credentials block puts "
            "both halves one line apart, and redacting only the id is theatre."
        ),
    ),
    Rule(
        name="named-secret-assignment",
        # PROVENANCE_CAPABILITY_HMAC_KEY=..., CURSOR_HMAC_KEY=...,
        # INGEST_ALIAS_HMAC_KEY=..., COGNITO_..._CLIENT_SECRET=..., *_TOKEN=...
        #
        # The suffix list is closed on purpose. A generic `[A-Z_]+=\S+` rule
        # would eat PV_SABOTAGE, PV_GIT_SHA, PV_RETRIEVAL_MODE and every other
        # configuration line a gate log exists to show. Note that `_ARN` is not
        # a member: settings.py records that an ARN is a reference, not a
        # credential, and G13.6 asserts on the ARNs bound to App Runner.
        pattern=re.compile(
            r"\b(?P<kw>[A-Z][A-Z0-9_]*"
            r"(?:HMAC_KEY|SIGNING_KEY|PRIVATE_KEY|SECRET_KEY|_API_KEY|_SECRET|_TOKEN|_PASSWORD))"
            + _SEP
            + r"[:=]"
            + _SEP
            + r"(?![\s$<{])(?P<secret>"
            + _VALUE
            + r")"
        ),
        replacement=r"\g<kw>=[REDACTED-SECRET]",
        why=(
            "The three HMAC keys in provenance_contracts.settings are symmetric signing "
            "keys: possession is forgery of a capability token or a pagination cursor. "
            "They are named in 40_INFRA_IAC.md section 12 and printed by any env dump."
        ),
    ),
)


def scrub_line(line: str) -> str:
    """Apply every rule to one line, in order."""
    for rule in RULES:
        line = rule.pattern.sub(rule.replacement, line)
    return line


def scrub_text(text: str) -> str:
    """Apply every rule to a whole string.

    Line-oriented by construction, so the result of scrubbing a transcript is
    the concatenation of scrubbing its lines. `splitlines(keepends=True)`
    preserves the exact line terminators, so a clean transcript comes back
    byte-identical - which `tools/tests/test_scrub.py` asserts, because a log
    whose line count moved is a log whose `grep -c` assertions moved with it.
    """
    return "".join(scrub_line(line) for line in text.splitlines(keepends=True))


def _iter_scrubbed(stream: Iterable[str]) -> Iterator[str]:
    for line in stream:
        yield scrub_line(line)


def _configure(stream: object, **kwargs: object) -> None:
    """Best-effort text-stream reconfiguration.

    `surrogateescape` makes undecodable bytes survive the round trip rather than
    being replaced, so a gate log is byte-faithful even when a child process
    emits something that is not UTF-8. `newline=""` disables newline
    translation, so a `\\n` transcript stays a `\\n` transcript on Windows, where
    half of this build's commands run.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        # A stream that cannot be reconfigured is not a reason to lose a gate log.
        with contextlib.suppress(ValueError, OSError):
            reconfigure(**kwargs)


def _print_rules(out: object) -> None:
    write = out.write  # type: ignore[attr-defined]
    write(f"tools/scrub.py - {len(RULES)} rules\n\n")
    for rule in RULES:
        write(f"  {rule.name}\n")
        write(f"    pattern     {rule.pattern.pattern}\n")
        write(f"    replacement {rule.replacement}\n")
        write(f"    why         {rule.why}\n\n")
    write(
        "Every rule has a leak case in tools/tests/test_scrub.py that fails if the\n"
        "rule is deleted, and the false-positive cases there name the real values\n"
        "this build prints that must survive untouched.\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/scrub.py",
        description=(
            "Redact credentials from gate evidence before it is committed "
            "(quality/23_PHASE_GATES.md section 2.2)."
        ),
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="files to filter to stdout; with none, filter stdin (the tools/gate.sh path)",
    )
    parser.add_argument(
        "--rules",
        action="store_true",
        help="print the rule table and exit; nothing is read",
    )
    args = parser.parse_args(argv)

    _configure(sys.stdout, encoding="utf-8", errors="surrogateescape", newline="")

    if args.rules:
        _print_rules(sys.stdout)
        return 0

    try:
        if args.files:
            for name in args.files:
                path = Path(name)
                with path.open("r", encoding="utf-8", errors="surrogateescape", newline="") as fh:
                    for line in _iter_scrubbed(fh):
                        sys.stdout.write(line)
        else:
            _configure(sys.stdin, encoding="utf-8", errors="surrogateescape", newline="")
            for line in _iter_scrubbed(sys.stdin):
                sys.stdout.write(line)
        sys.stdout.flush()
    except BrokenPipeError:  # pragma: no cover - downstream closed early
        return 0
    except OSError as exc:
        sys.stderr.write(f"tools/scrub.py: {exc}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
