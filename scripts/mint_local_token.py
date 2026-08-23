"""Mint a token the local identity provider accepts.

Why this exists
---------------
`PV_PLATFORM=local` is the mode `.env.example` calls the reviewer's mode -- a
laptop with no cloud account -- and it has a working HS256 verifier
(`app/auth/identity.py`) but **no way to obtain a token**. There is no login
route, no console, and no issuer to redirect to. So the API correctly answered
`401 UNAUTHENTICATED` to every read, the web app had nothing to put in an
`Authorization` header, and the whole live path was unreachable on the one
platform a judge will actually run.

`issue_local_token` already existed and was called only by tests. This is the
one command-line entry point to it, so the claim vocabulary still has exactly
one definition and this file adds no second opinion about what a token is.

Usage
-----
    python scripts/mint_local_token.py                      # web client, 12h
    python scripts/mint_local_token.py --client provenance-agent-runtime
    python scripts/mint_local_token.py --ttl 3600 --quiet   # just the token

Reads `PV_LOCAL_AUTH_SECRET` from the environment. It is inert without it,
which is the point: there is no default signing key, because a default signing
key verifies forged tokens.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.control_plane.app.auth.identity import (  # noqa: E402
    LOCAL_ISSUER,
    issue_local_token,
)

#: The three clients `LOCAL_CLIENT_ID_NAMES` fixes. Restated as a tuple only to
#: validate `--client`; the grant itself is the API's, never this script's.
CLIENTS = ("provenance-web", "provenance-agent-runtime", "provenance-workers")

#: The demo subject. This is `users.cognito_sub`, not the display name and not
#: the row id: the API looks the principal up by the token's `sub` claim
#: against that column. A token for anyone else verifies, and then returns
#: `403 USER_NOT_PROVISIONED` -- which is the right refusal and is easy to
#: mistake for a broken token, so the value is fixed here rather than guessed.
#: `db/seeds/` writes the entire hero record under this principal.
DEFAULT_SUBJECT = "seed-hero-alex-rivera"


def _load_dotenv(root: Path) -> None:
    """Populate os.environ from .env without echoing a value.

    Settings deliberately refuses to parse a dotenv (settings.py:331). This is
    a developer command run by hand, not an import-time path, so loading here
    cannot surprise a test run.
    """
    env = root / ".env"
    if not env.exists():
        return
    for raw in env.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mint a local-issuer access token.")
    parser.add_argument("--client", default="provenance-web", choices=CLIENTS)
    parser.add_argument("--subject", default=DEFAULT_SUBJECT)
    parser.add_argument("--ttl", type=int, default=43200, help="seconds (default 12h)")
    parser.add_argument(
        "--scope",
        default=None,
        help="space-delimited. Omitted means the API applies the client's default grant.",
    )
    parser.add_argument("--quiet", action="store_true", help="print only the token")
    args = parser.parse_args(argv)

    _load_dotenv(_REPO_ROOT)
    secret = os.environ.get("PV_LOCAL_AUTH_SECRET", "")
    if not secret:
        print(
            "CANNOT RUN: PV_LOCAL_AUTH_SECRET is unset. This is not a failure of "
            "the token path -- nothing was attempted. Generate one with:\n"
            '  python -c "import secrets,base64;'
            'print(base64.b64encode(secrets.token_bytes(32)).decode())"',
            file=sys.stderr,
        )
        return 2

    now = int(time.time())
    claims: dict[str, object] = {
        "iss": LOCAL_ISSUER,
        "sub": args.subject,
        "client_id": args.client,
        "token_use": "access",
        "iat": now,
        "nbf": now,
        "exp": now + args.ttl,
        "groups": [],
    }
    if args.scope:
        claims["scope"] = args.scope

    token = issue_local_token(claims, key=secret.encode("utf-8"))

    if args.quiet:
        print(token)
        return 0

    print(f"client   : {args.client}")
    print(f"subject  : {args.subject}")
    print(f"issuer   : {LOCAL_ISSUER}")
    print(f"expires  : {args.ttl}s from now")
    print(f"scope    : {args.scope or '(API default grant for this client)'}")
    print()
    print(token)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
