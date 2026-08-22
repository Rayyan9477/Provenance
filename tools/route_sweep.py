"""Walk every route the live web app can reach and report which ones break.

Why this exists
---------------
The frontend had 65 passing component tests and 20 routes, and **nine of those
routes crashed** the first time anything loaded them against the real API. The
tests could not have caught it: every one of the nine failed on a shape the
contract declared and the server did not send, and both the fixtures and the
types were written from the same reading of the spec, so they agreed with each
other and disagreed with the API.

    committed_amount   declared a decimal string, sent as {currency, amount}
    parser_metadata    declared a Record,          sent as null
    context            declared present,           sent as null
    outstanding_amount declared Money,             sent as null on a
                       non-monetary commitment

Every one of those took a whole route to `500`. A judge opening the app would
have found a case docket that said "Application error: a server-side exception
has occurred".

What makes this different from a list of URLs
---------------------------------------------
The ids come from the API, so the sweep visits every case, every relationship
and every artifact the corpus actually contains rather than the two somebody
remembered to write down. A route that works for one id and dies on another --
which is exactly what happened, since only cases *inside* a context rendered --
is caught.

Two measurement traps, both of which produced a wrong answer here before being
fixed:

1. **`"This page could not be found"` matches every page.** Next.js serialises
   its 404 boundary into the RSC payload of every route, so grepping for it
   reports 100% broken. `next-error-h1` is in the shared error CSS and does the
   same. The discriminator has to be POSITIVE: a real page renders the app
   shell and a 404 does not (measured: 2 occurrences against 0).
2. **A cold dev server looks like a broken one.** First hit compiles the route
   and can exceed a short timeout, which reads as a failure. The timeout is
   generous and `--warm` visits everything once before judging.

Usage
-----
    python -m tools.route_sweep
    python -m tools.route_sweep --web http://localhost:3000 --api http://127.0.0.1:8080
    python -m tools.route_sweep --warm

Exit codes: ``0`` every route rendered; ``1`` at least one did not; ``2`` the
sweep could not run (no API, no token), which is NOT the same as a broken route
and must never be recorded as one.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

#: A rendered error the app itself produced.
ERROR_MARKERS = ("Application error", "a server-side exception has occurred")

#: Present on every route inside the `(app)` group and absent from a 404.
#: This is the positive discriminator; see the module docstring for why the
#: obvious negative ones do not work.
SHELL_MARKER = "pv-shell"

#: Routes that legitimately render without the app shell. `/login` sits outside
#: the `(app)` group on purpose -- there is no navigation to offer someone who
#: is not signed in -- so its missing shell is a design decision, not a break.
SHELL_EXEMPT = frozenset({"/login"})

STATIC_ROUTES = (
    "/dashboard",
    "/cases",
    "/relationships",
    "/proof",
    "/ingest",
    "/artifacts",
    "/watches",
    "/search",
    "/actions",
    "/export",
    "/judge",
    "/judge/counterfactual",
    "/settings",
    "/login",
)


def _token() -> str | None:
    result = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "mint_local_token.py"), "--quiet"],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _api(base: str, path: str, token: str, timeout: int) -> Any:
    request = urllib.request.Request(f"{base}{path}", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _web(base: str, path: str, timeout: int) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, f"__unreachable__ {type(exc).__name__}: {exc}"


def _ids(payload: Any, key: str) -> list[str]:
    if not isinstance(payload, dict):
        return []
    return [item[key] for item in payload.get("items", []) if key in item]


def discover(api_base: str, token: str, timeout: int) -> list[str]:
    """Every route worth visiting, with ids read from the API."""
    routes = list(STATIC_ROUTES)
    cases = _ids(_api(api_base, "/v1/cases?limit=50", token, timeout), "case_id")
    rels = _ids(_api(api_base, "/v1/relationships?limit=50", token, timeout), "relationship_id")
    arts = _ids(_api(api_base, "/v1/artifacts?limit=50", token, timeout), "artifact_id")
    routes += [f"/cases/{i}" for i in cases]
    routes += [f"/cases/{i}/proof" for i in cases]
    routes += [f"/relationships/{i}" for i in rels]
    # Artifacts are capped: the corpus holds 18,035 and they exercise one
    # template. The cap is stated in the output rather than left silent, because
    # a sweep that quietly skips most of its subject reads as full coverage.
    routes += [f"/artifacts/{i}" for i in arts[:10]]
    return routes


def check(web_base: str, route: str, timeout: int) -> list[str]:
    """Every reason this route is broken, or an empty list."""
    status, body = _web(web_base, route, timeout)
    problems = [m for m in ERROR_MARKERS if m in body]
    if status != 200:
        problems.append(f"HTTP {status}")
    if SHELL_MARKER not in body and route not in SHELL_EXEMPT:
        problems.append("no app shell (404, or the route did not render)")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web", default="http://localhost:3000")
    parser.add_argument("--api", default="http://127.0.0.1:8080")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--warm",
        action="store_true",
        help="visit every route once before judging, so a cold compile is not read as a break",
    )
    args = parser.parse_args(argv)

    token = _token()
    if token is None:
        print(
            "CANNOT RUN: could not mint a token. PV_LOCAL_AUTH_SECRET is probably "
            "unset. No route was visited, so no route is either confirmed or "
            "denied -- this is NOT a failing sweep.",
            file=sys.stderr,
        )
        return 2

    if _api(args.api, "/v1/version", token, 15) is None:
        print(
            f"CANNOT RUN: no API at {args.api}. Start it with `make run-api`. "
            "Nothing was visited; this is not a failing sweep.",
            file=sys.stderr,
        )
        return 2

    routes = discover(args.api, token, args.timeout)
    print(f"discovered {len(routes)} routes (artifact detail capped at 10)")

    if args.warm:
        print("warming...")
        for route in routes:
            _web(args.web, route, args.timeout)

    broken: list[tuple[str, list[str]]] = []
    for route in routes:
        problems = check(args.web, route, args.timeout)
        if problems:
            broken.append((route, problems))
            print(f"  BROKEN  {route}")
            for problem in problems:
                print(f"            {problem}")

    print(f"\nswept {len(routes)}  broken {len(broken)}")
    if broken:
        print(
            "\nA broken route here is a route a judge can reach. The web test suite "
            "cannot see these: it runs against fixtures, and every failure of this "
            "kind was a shape the fixture and the type agreed on and the server did not."
        )
    return 1 if broken else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
