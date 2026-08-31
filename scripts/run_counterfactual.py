"""Run the Judge Mode counterfactual against the live cluster and live Gemini.

Why this file exists
--------------------
``STATUS.md`` recorded ``write.start_counterfactual``, ``write.get_counterfactual``
and ``write.run_probe`` as unbound on "the agent runtime", and
``ops/41_RUNBOOK.md`` section 8.1 step 8 -- the demo's closing argument -- as
BLOCKED on them. The hermetic suite proves the orchestration; it cannot prove
that the two ``agent_runs`` rows satisfy CHECK constraints on the real cluster,
that ``CounterfactualReading`` survives ``google-genai``'s schema conversion, or
that ``cases.revision`` is unmoved by a run that actually happened. Only a live
run answers those, and only a live run can be quoted.

Usage
-----
    python scripts/run_counterfactual.py --list
    python scripts/run_counterfactual.py --artifact northline-final-invoice
    python scripts/run_counterfactual.py --artifact northline-final-invoice \\
        --transcript ops/counterfactual-live-run.txt
    python scripts/run_counterfactual.py --probe-only

What it writes, and under which grant
--------------------------------------
Two ``agent_runs`` rows per counterfactual, ``INSERT`` then ``UPDATE``, as
``pv_app_reader_writer`` -- the same grant and the same table
``scripts/run_ingestion_graph.py`` uses, and ``agent_runs`` is deliberately
absent from ``tools/write_path_lint.CANONICAL_TABLES``. **Nothing else.** The
whole point of the exercise is that ``cases.revision`` is identical before and
after, and this script reads it from a fresh connection on either side rather
than asserting it.

Windows
-------
``psycopg`` async refuses the proactor loop, which is the default event loop on
Windows. Every ``asyncio.run`` below therefore passes a selector loop factory.
That is a reason, not a superstition: without it the connection fails with
``NotImplementedError`` from inside ``add_reader``.

``CANNOT RUN`` is not ``FAIL``
------------------------------
``D-00-005``. Three verdicts, counted separately. A run that could not reach a
model is not a run that produced a wrong answer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import selectors
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import psycopg

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ruff: noqa: E402  -- sys.path must be primed before the first-party imports.
from scripts.mint_local_token import _load_dotenv
from scripts.seed.db import role_dsn
from services.control_plane.app.api.adapters.read import SqlReadPort
from services.control_plane.app.api.ports import OwnerScope
from services.control_plane.app.counterfactual.service import CounterfactualService
from services.control_plane.app.counterfactual.sql import SqlCounterfactualStore
from services.control_plane.app.counterfactual.wiring import default_probe_service, live_router

PASS: Final[str] = "PASS"
FAIL: Final[str] = "FAIL"
CANNOT_RUN: Final[str] = "CANNOT RUN"

#: ``CANONICAL_DECISIONS.md`` -> *Hero user*.
HERO_SUB: Final[str] = "seed-hero-alex-rivera"

#: The artifact the demo would use is ``northline-june-invoice.eml``, and it is
#: deliberately **not** seeded -- ``scripts/seed/evidence.py`` records that it is
#: uploaded live at demo time, and the upload path is a different lane. This is
#: the sharpest contradiction available among the artifacts that *are* in
#: ``source_artifacts``: an invoice on a case whose record carries a payment
#: confirmation and a zero-balance statement.
DEFAULT_SLUG: Final[str] = "northline-final-invoice"


class Transcript:
    """Prints and, optionally, writes. Counts the three verdicts separately."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._lines: list[str] = []
        self.counts = {PASS: 0, FAIL: 0, CANNOT_RUN: 0}

    def say(self, line: str = "") -> None:
        print(line)
        self._lines.append(line)

    def verdict(self, verdict: str, what: str, detail: str = "") -> None:
        self.counts[verdict] += 1
        self.say(f"  [{verdict:<10}] {what}" + (f"  --  {detail}" if detail else ""))

    def flush(self) -> None:
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text("\n".join(self._lines) + "\n", encoding="utf-8")
            print(f"\ntranscript written to {self._path}")


class AsyncRolePool:
    """A ``ConnectionSource`` over one role, one connection per ``connection()``.

    Not a pool in the ``provenance_db.pools`` sense, and deliberately so: that
    one is built from ``Settings``, and ``Settings`` does not read ``.env``
    (``settings.py:331``). This opens the same DSN the seed opens, asserts the
    role it authenticated as -- the cheapest proof the grant boundary is real --
    and gives every call its own connection, which is what makes the
    before/after revision reads two observations rather than one snapshot.
    """

    def __init__(self, role: str) -> None:
        self._role = role
        self._dsn = str(role_dsn(role))

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[Any]:
        conn = await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)
        try:
            async with conn.cursor() as cur:
                await cur.execute("SELECT current_user")
                row = await cur.fetchone()
            actual = str(row[0]) if row else "<unknown>"
            if actual != self._role:
                raise RuntimeError(f"connection for {self._role!r} authenticated as {actual!r}")
            yield conn
        finally:
            await conn.close()


def _run(coro: Any) -> Any:
    """Windows: psycopg async refuses the proactor loop. See the docstring."""
    return asyncio.run(
        coro, loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
    )


async def _owner(pool: AsyncRolePool, sub: str) -> OwnerScope:
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT tenant_id, id FROM users WHERE cognito_sub = %s", (sub,))
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"no user with cognito_sub={sub!r}")
    return OwnerScope(tenant_id=row[0], user_id=row[1])


async def _artifacts(pool: AsyncRolePool, scope: OwnerScope) -> list[tuple[str, uuid.UUID]]:
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            SELECT s3_key, id FROM source_artifacts
            WHERE tenant_id = %s AND user_id = %s AND s3_key LIKE 'raw/hero/hero/%%'
            ORDER BY received_at
            """,
            (scope.tenant_id, scope.user_id),
        )
        rows = await cur.fetchall()
    return [(Path(str(key)).stem, artifact_id) for key, artifact_id in rows]


async def _case_revisions(pool: AsyncRolePool, scope: OwnerScope) -> dict[str, int]:
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT id, revision FROM cases WHERE tenant_id = %s AND user_id = %s ORDER BY id",
            (scope.tenant_id, scope.user_id),
        )
        rows = await cur.fetchall()
    return {str(case_id): int(revision) for case_id, revision in rows}


async def _agent_run_count(pool: AsyncRolePool) -> int:
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT count(*) FROM agent_runs")
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _drive(args: argparse.Namespace, transcript: Transcript) -> int:
    pool = AsyncRolePool("pv_app_reader_writer")
    scope = await _owner(pool, args.user)
    transcript.say(f"owner scope         : tenant {scope.tenant_id}  user {scope.user_id}")

    seeded = await _artifacts(pool, scope)
    if args.list:
        for slug, artifact_id in seeded:
            transcript.say(f"  {slug:<45} {artifact_id}")
        return 0

    by_slug = dict(seeded)
    if args.artifact not in by_slug:
        transcript.verdict(
            CANNOT_RUN,
            "artifact resolution",
            f"{args.artifact!r} is not a seeded source_artifacts row; --list shows what is",
        )
        return 2
    artifact_id = by_slug[args.artifact]
    transcript.say(f"artifact            : {args.artifact}  ({artifact_id})")
    transcript.say()

    read = SqlReadPort(pool, feature_flags={}, clock=lambda: datetime.now(UTC))
    store = SqlCounterfactualStore(pool, read=read)
    service = CounterfactualService(
        store=store, router_factory=live_router, clock=lambda: datetime.now(UTC)
    )

    # -- CF1  the state before ------------------------------------------------
    transcript.say("-- CF1  canonical state before the counterfactual")
    revisions_before = await _case_revisions(pool, scope)
    runs_before = await _agent_run_count(pool)
    transcript.verdict(
        PASS,
        "cases.revision read for every case",
        f"{len(revisions_before)} cases; agent_runs holds {runs_before} rows",
    )

    # -- CF2  the run ---------------------------------------------------------
    transcript.say()
    transcript.say("-- CF2  POST /v1/judge-mode/counterfactual (both modes, live model)")
    started = datetime.now(UTC)
    body = await service.start(
        scope,
        _Payload(artifact_id=artifact_id, modes=None, memory_on_strategy=args.strategy),
    )
    elapsed = (datetime.now(UTC) - started).total_seconds()
    if body is None:
        transcript.verdict(FAIL, "start_counterfactual", "returned None for an owned artifact")
        return 1
    if body["status"] == "FAILED" and "error" in body:
        transcript.verdict(
            CANNOT_RUN, f"start_counterfactual -> {body['error']['code']}", body["error"]["message"]
        )
    else:
        transcript.verdict(
            PASS if body["status"] == "COMPLETED" else FAIL,
            f"start_counterfactual -> {body['status']}",
            f"{elapsed:.1f}s, counterfactual_id={body['counterfactual_id']}",
        )
    counterfactual_id = uuid.UUID(body["counterfactual_id"])

    # -- CF3  the rows the cluster accepted -----------------------------------
    transcript.say()
    transcript.say("-- CF3  the two agent_runs rows, read back from the cluster")
    runs_after = await _agent_run_count(pool)
    rows = await store.read_pair(scope, counterfactual_id)
    if len(rows) == 2:
        transcript.verdict(
            PASS,
            "two agent_runs rows written and accepted by every CHECK",
            f"agent_runs {runs_before} -> {runs_after}",
        )
    else:
        transcript.verdict(FAIL, "agent_runs pair", f"{len(rows)} rows, expected 2")
    for row in rows:
        transcript.say(
            f"      {row['memory_mode']:<3} is_counterfactual={row['is_counterfactual']} "
            f"graph={row['graph_name']}/{row['graph_version']} status={row['status']} "
            f"allowed_case_ids={row['allowed_case_ids']} "
            f"corpus_visible={row['retrieval_candidate_count']}"
        )

    # -- CF4  the poll --------------------------------------------------------
    transcript.say()
    transcript.say("-- CF4  GET /v1/judge-mode/counterfactual/{id}")
    poll = await service.get(scope, counterfactual_id)
    if poll is None:
        transcript.verdict(FAIL, "get_counterfactual", "returned None for a run it just wrote")
        return 1
    parity = poll["parity"]
    transcript.verdict(
        PASS if parity["all_equal"] else FAIL,
        f"parity.all_equal = {parity['all_equal']}",
        ", ".join(f"{k}={v['equal']}" for k, v in parity.items() if isinstance(v, dict)),
    )
    for side in ("memory_off", "memory_on"):
        block = poll.get(side) or {}
        output = block.get("output")
        transcript.say(f"      {side}:")
        transcript.say(f"        model_id            : {block.get('model_id')}")
        transcript.say(f"        retrieval_enabled   : {block.get('retrieval_enabled')}")
        transcript.say(f"        corpus_size_visible : {block.get('corpus_size_visible')}")
        transcript.say(f"        duration_ms         : {block.get('duration_ms')}")
        if output is None:
            transcript.say(f"        output              : (none) {block.get('error')}")
            continue
        transcript.say(f"        headline            : {output['headline']}")
        transcript.say(f"        classification      : {output['classification']}")
        transcript.say(f"        conflicts_detected  : {output['conflicts_detected']}")
        transcript.say(f"        recommended_action  : {output['recommended_action']}")
        transcript.say(f"        support_ids         : {output['support_ids']}")
        if output["draft_text"]:
            for line in output["draft_text"].splitlines():
                transcript.say(f"        draft | {line}")
        transcript.say(f"        why                 : {block.get('why')}")
    transcript.say(f"      delta   : {json.dumps(poll.get('delta'), default=str)}")

    # -- CF5  the safety assertion the runbook makes --------------------------
    transcript.say()
    transcript.say("-- CF5  ops/41_RUNBOOK.md 8.1 step 8: the record must be unmoved")
    revisions_after = await _case_revisions(pool, scope)
    moved = {
        case_id: (before, revisions_after.get(case_id))
        for case_id, before in revisions_before.items()
        if revisions_after.get(case_id) != before
    }
    transcript.verdict(
        PASS if not moved else FAIL,
        "cases.revision identical before and after, every case",
        f"{len(revisions_before)} cases compared" if not moved else f"moved: {moved}",
    )
    safety = poll["safety"]
    transcript.verdict(
        PASS if safety["case_revision_changed_by_counterfactual"] is False else FAIL,
        "safety.case_revision_changed_by_counterfactual == false",
        f"before={safety.get('case_revision_before')} after={safety.get('case_revision_after')} "
        f"measured={safety.get('measured')}",
    )
    for flag in (
        "memory_off_wrote_canonical_state",
        "memory_off_admitted_evidence",
        "memory_off_had_proposal_tool",
    ):
        transcript.verdict(PASS if safety[flag] is False else FAIL, f"safety.{flag} == false")
    return 0


class _Payload:
    """The section 8.30 request body, without a FastAPI round trip."""

    def __init__(self, *, artifact_id: uuid.UUID, modes: Any, memory_on_strategy: str) -> None:
        self.artifact_id = artifact_id
        self.modes = modes
        self.memory_on_strategy = memory_on_strategy


async def _probe(transcript: Transcript) -> int:
    transcript.say("-- PB  POST /v1/judge-mode/probes (section 8.33)")
    service = default_probe_service(clock=lambda: datetime.now(UTC))
    body = await service.run(_ProbePayload())
    for result in body["results"]:
        verdict = {"PASS": PASS, "FAIL": FAIL, "CANNOT_RUN": CANNOT_RUN}[result["verdict"]]
        transcript.verdict(verdict, str(result["model_id"]), str(result["detail"]))
    transcript.say(f"      counts  : {body['counts']}  status={body['status']}")
    return 0 if body["counts"]["FAIL"] == 0 else 1


class _ProbePayload:
    probe_type = "MODEL_AVAILABILITY"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", default=DEFAULT_SLUG)
    parser.add_argument("--user", default=HERO_SUB)
    parser.add_argument(
        "--strategy", default="REPLAY_COMMITTED", choices=["REPLAY_COMMITTED", "RERUN_SANDBOXED"]
    )
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--transcript", type=Path, default=None)
    args = parser.parse_args()

    _load_dotenv(_REPO_ROOT)
    transcript = Transcript(args.transcript)
    transcript.say("=" * 78)
    transcript.say("Provenance -- live Judge Mode counterfactual  (CF1..CF5, PB)")
    transcript.say(f"started             : {datetime.now(UTC).isoformat()}")
    transcript.say("service             : app/counterfactual/service.py (the bound port)")
    transcript.say("graph               : agents/runtime/graphs/counterfactual_graph.py")
    transcript.say("api                 : Gemini Developer API (AI Studio key)")
    transcript.say("verdict semantics   : PASS / FAIL / CANNOT RUN are THREE outcomes (D-00-005).")
    transcript.say("=" * 78)
    transcript.say()

    try:
        code = _run(_probe(transcript)) if args.probe_only else _run(_drive(args, transcript))
    except Exception as exc:
        transcript.verdict(FAIL, f"unhandled {type(exc).__name__}", str(exc))
        code = 1

    transcript.say()
    transcript.say("=" * 78)
    transcript.say(
        f"PASS {transcript.counts[PASS]}  FAIL {transcript.counts[FAIL]}  "
        f"CANNOT RUN {transcript.counts[CANNOT_RUN]}"
    )
    transcript.say("=" * 78)
    transcript.flush()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
