"""Sections 8.30 and 8.31: the memory ON/OFF counterfactual, executed for real.

Authority
---------
- ``docs/specs/15_API_SPEC.md`` sections 8.30 and 8.31.
- ``docs/CANONICAL_DECISIONS.md`` -> *Counterfactual*, *Counterfactual prompt*,
  *Counterfactual parity canon*, *Judge Mode*.
- ``docs/ops/41_RUNBOOK.md`` section 8.1 step 8.
- ``agents/runtime/graphs/counterfactual_graph.py`` -- the graph both sides walk.

The shape of the thing
-----------------------
Two walks of one graph, in this order: MEMORY OFF, then MEMORY ON. Around them,
four measurements taken from the database on either side -- the case revision
and the case's evidence count, before and again after -- because
``safety.case_revision_changed_by_counterfactual`` is the guarantee that the
demo's most dramatic step cannot damage the record it is demonstrating, and a
constant ``false`` would satisfy the runbook while proving nothing.

Synchronous, and it says ``COMPLETED``
---------------------------------------
Section 8.30's example body says ``RUNNING`` because it was written for a
queued execution. Nothing in this build has a queue, and inventing one to
return ``RUNNING`` truthfully would be a worse answer than running the two
calls and reporting what happened. ``status`` is therefore already terminal
when the ``202`` is written, the ``poll_url`` still resolves, and section
8.31's ``status`` domain (``RUNNING | COMPLETED | FAILED | PARTIAL``) is
unchanged -- ``RUNNING`` is simply a state this implementation never occupies.

The model call is not inside a transaction
--------------------------------------------
``python -m tools.txn_purity_lint`` exists because a network call inside a
serializable transaction callback holds a lock across a model's latency and
turns every retry into a re-invocation. Nothing here opens a transaction: the
pooled connections are autocommit, each statement lands on its own, and the two
model calls happen between statements rather than inside one.

What is persisted, and what deliberately is not
-------------------------------------------------
Two ``agent_runs`` rows carry the attribution and the configuration -- which is
what ``parity`` and ``safety`` are computed from, and what makes this "built
from persisted runtime rows" in the sense ``CANONICAL_DECISIONS.md`` -> *Judge
Mode* requires. The **readings are not persisted**: ``specs/14_PROMPTS.md``
section 6.4 says in terms that neither output is stored for replay and neither
is cached, and the applied schema has no column for one anyway. They are held
for the poll of the run that produced them and for nothing else, and
:meth:`CounterfactualService.get` says so in the response rather than returning
``null`` and letting the reader guess which kind of nothing it is.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Protocol

from agents.runtime.graphs.counterfactual_graph import (
    GRAPH_NAME_COUNTERFACTUAL,
    GRAPH_VERSION_COUNTERFACTUAL,
    PROMPT_VERSION,
    CounterfactualDeps,
    CounterfactualOutcome,
    CounterfactualState,
    initial_counterfactual_state,
    run_counterfactual,
)
from agents.runtime.prompts.render import AssetPromptRenderer
from agents.runtime.schemas.counterfactual import CounterfactualReading
from provenance_contracts.ingestion import ContentBlock
from services.control_plane.app.api.ports import OwnerScope
from services.control_plane.app.counterfactual import store as run_store
from services.control_plane.app.counterfactual.artifacts import (
    ArtifactBytesUnavailableError,
    LocalArtifactSource,
)

__all__ = [
    "MODES",
    "ArtifactFacts",
    "CaseFacts",
    "CounterfactualService",
    "CounterfactualStore",
    "SideResult",
]

#: MEMORY OFF first, always. The order is the demo's: the room sees the
#: memoryless reading before it sees the one that contradicts it.
MODES: Final[tuple[str, str]] = ("MEMORY_OFF", "MEMORY_ON")

#: ``ck_agent_runs_expiry`` requires ``expires_at > started_at``. Nothing
#: consumes this capability window -- no capability row is minted for either
#: run -- so the value is a bound on the row rather than a grant to anybody.
_RUN_WINDOW: Final[timedelta] = timedelta(minutes=15)

#: How many completed counterfactuals keep their readings for a poll. Small on
#: purpose: this is a poll buffer, not a cache. Section 14.1 rate-limits the
#: endpoint to ten per hour per user.
_RESULT_BUFFER: Final[int] = 16


@dataclass(frozen=True, slots=True)
class ArtifactFacts:
    """What ``source_artifacts`` says about the artifact both sides read."""

    artifact_id: uuid.UUID
    content_sha256: str
    mime_type: str
    subject: str | None
    summary: str


@dataclass(frozen=True, slots=True)
class CaseFacts:
    """The case the artifact's evidence reached, and its revision."""

    case_id: uuid.UUID
    title: str
    revision: int


@dataclass(frozen=True, slots=True)
class SideResult:
    """One side's reading, held for the poll. Never written to a table."""

    mode: str
    reading: CounterfactualReading | None
    outcome: str
    error_code: str | None
    duration_ms: int
    corpus_size_visible: int


class CounterfactualStore(Protocol):
    """Every database touch this service makes, in one boundary.

    A Protocol rather than a connection so the whole orchestration -- both
    graph walks, the parity computation and the safety measurement -- is
    drivable with no cluster, which is what makes "did it measure the revision
    or declare it?" a unit test.
    """

    async def artifact_facts(
        self, scope: OwnerScope, artifact_id: uuid.UUID
    ) -> ArtifactFacts | None: ...

    async def case_facts(self, scope: OwnerScope, artifact_id: uuid.UUID) -> CaseFacts | None: ...

    async def state_proof(
        self, scope: OwnerScope, case_id: uuid.UUID
    ) -> Mapping[str, Any] | None: ...

    async def case_revision(self, scope: OwnerScope, case_id: uuid.UUID) -> int | None: ...

    async def evidence_count(self, scope: OwnerScope, case_id: uuid.UUID) -> int: ...

    async def corpus_size(self, scope: OwnerScope) -> int: ...

    async def has_committed_draft(self, scope: OwnerScope, artifact_id: uuid.UUID) -> bool: ...

    async def open_run(self, params: Mapping[str, Any]) -> None: ...

    async def settle_run(self, params: Mapping[str, Any]) -> bool: ...

    async def read_pair(self, scope: OwnerScope, trace_id: uuid.UUID) -> list[dict[str, Any]]: ...


class _Binder:
    """The MEMORY ON side's trusted context, resolved once before the walk.

    Resolved before rather than during because the graph is synchronous and the
    State Proof read is not. The MEMORY OFF side never constructs one of these
    at all -- ``bind_memory`` substitutes the empty block without consulting a
    binder, so there is no object through which memory could reach it.
    """

    __slots__ = ("_context",)

    def __init__(self, context: Mapping[str, Any]) -> None:
        self._context = context

    def bind(self, mode: str) -> Mapping[str, Any]:
        if mode != "MEMORY_ON":
            raise AssertionError(f"the memory binder was consulted under {mode}")
        return self._context


class CounterfactualService:
    """Sections 8.30 and 8.31, over the real graph and the real router."""

    __slots__ = ("_artifacts", "_clock", "_renderer", "_results", "_router_factory", "_store")

    def __init__(
        self,
        *,
        store: CounterfactualStore,
        router_factory: Callable[[], Any],
        artifacts: Any = None,
        renderer: Any = None,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._router_factory = router_factory
        self._artifacts = artifacts if artifacts is not None else LocalArtifactSource()
        self._renderer = renderer if renderer is not None else AssetPromptRenderer()
        self._clock = clock
        self._results: dict[uuid.UUID, dict[str, Any]] = {}

    # -- 8.30 -------------------------------------------------------------

    async def start(self, scope: OwnerScope, payload: Any) -> dict[str, Any] | None:
        """Run both sides. ``None`` means the artifact is not this caller's."""
        artifact_id = payload.artifact_id
        artifact = await self._store.artifact_facts(scope, artifact_id)
        if artifact is None:
            return None

        counterfactual_id = uuid.uuid4()
        started_at = self._clock()
        modes = _requested_modes(payload)
        case = await self._store.case_facts(scope, artifact_id)

        revision_before = (
            await self._store.case_revision(scope, case.case_id) if case is not None else None
        )
        evidence_before = (
            await self._store.evidence_count(scope, case.case_id) if case is not None else 0
        )

        try:
            blocks = self._artifacts.blocks_for(
                artifact_id=artifact.artifact_id,
                content_sha256=artifact.content_sha256,
                mime_type=artifact.mime_type,
                subject=artifact.subject,
            )
        except ArtifactBytesUnavailableError as refusal:
            return await self._record_refusal(
                scope,
                counterfactual_id=counterfactual_id,
                artifact=artifact,
                case=case,
                modes=modes,
                started_at=started_at,
                code="ARTIFACT_BYTES_UNAVAILABLE",
                detail=str(refusal),
            )

        # Built once, before either run is opened, and the failure to build one
        # is a refusal rather than a 500: "no model is configured in this
        # process" is a fact about the deployment that belongs in the ledger.
        try:
            router = self._router_factory()
        except Exception as refusal:
            return await self._record_refusal(
                scope,
                counterfactual_id=counterfactual_id,
                artifact=artifact,
                case=case,
                modes=modes,
                started_at=started_at,
                code="MODEL_UNCONFIGURED",
                detail=f"{type(refusal).__name__}: {refusal}",
            )

        strategy, strategy_reason = await self._strategy(scope, payload, artifact_id)
        corpus_size = await self._store.corpus_size(scope)
        proof = await self._store.state_proof(scope, case.case_id) if case is not None else None
        binder = _Binder(
            _memory_on_context(case=case, proof=proof, corpus_size=corpus_size, artifact=artifact)
        )

        sides: dict[str, SideResult] = {}
        run_ids: dict[str, uuid.UUID] = {}
        for mode in modes:
            run_id = uuid.uuid4()
            run_ids[mode] = run_id
            visible = corpus_size if mode == "MEMORY_ON" else 0
            await self._store.open_run(
                run_store.insert_params(
                    run_id=run_id,
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    trace_id=counterfactual_id,
                    graph_name=GRAPH_NAME_COUNTERFACTUAL,
                    graph_version=GRAPH_VERSION_COUNTERFACTUAL,
                    model_route=_model_route(),
                    memory_mode="ON" if mode == "MEMORY_ON" else "OFF",
                    started_at=started_at,
                    expires_at=started_at + _RUN_WINDOW,
                    artifact_id=artifact.artifact_id,
                    # Section 8.30 safety property 1: the MEMORY OFF run is
                    # scoped to no case at all.
                    allowed_case_ids=([str(case.case_id)] if mode == "MEMORY_ON" and case else []),
                    corpus_size_visible=visible,
                    capability_status=_capability_status(
                        mode=mode, visible=visible, artifact=artifact, strategy=strategy
                    ),
                )
            )
            began = self._clock()
            state = await asyncio.to_thread(
                _walk,
                mode=mode,
                artifact=artifact,
                blocks=blocks,
                router=router,
                renderer=self._renderer,
                binder=binder,
            )
            finished = self._clock()
            duration_ms = max(int((finished - began).total_seconds() * 1000), 0)
            sides[mode] = SideResult(
                mode=mode,
                reading=state.reading,
                outcome=(state.outcome or CounterfactualOutcome.CANNOT_RUN).value,
                error_code=state.errors[0].code if state.errors else None,
                duration_ms=duration_ms,
                corpus_size_visible=visible,
            )
            await self._store.settle_run(
                run_store.settle_params(
                    run_id=run_id,
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    status="SUCCEEDED" if state.reading is not None else "FAILED",
                    error_code=None if state.reading is not None else _code(state),
                    finished_at=finished,
                    model_calls=[call.as_agent_runs_element() for call in state.calls],
                    capability_status=_capability_status(
                        mode=mode,
                        visible=visible,
                        artifact=artifact,
                        strategy=strategy,
                        state=state,
                    ),
                )
            )

        revision_after = (
            await self._store.case_revision(scope, case.case_id) if case is not None else None
        )
        evidence_after = (
            await self._store.evidence_count(scope, case.case_id) if case is not None else 0
        )

        self._remember(
            counterfactual_id,
            {
                "sides": sides,
                "case": case,
                "artifact": artifact,
                "strategy": strategy,
                "strategy_reason": strategy_reason,
                "revision_before": revision_before,
                "revision_after": revision_after,
                "evidence_before": evidence_before,
                "evidence_after": evidence_after,
                "run_ids": run_ids,
            },
        )
        return {
            "counterfactual_id": str(counterfactual_id),
            "status": _status(sides, modes),
            "artifact_id": str(artifact.artifact_id),
            "poll_url": f"/v1/judge-mode/counterfactual/{counterfactual_id}",
            "suggested_interval_ms": 1000,
        }

    # -- 8.31 -------------------------------------------------------------

    async def get(self, scope: OwnerScope, counterfactual_id: uuid.UUID) -> dict[str, Any] | None:
        """Both sides, gated on a parity block computed from persisted rows."""
        rows = await self._store.read_pair(scope, counterfactual_id)
        if not rows:
            return None
        held = self._results.get(counterfactual_id)
        by_mode = {
            ("MEMORY_ON" if str(row["memory_mode"]) == "ON" else "MEMORY_OFF"): row for row in rows
        }
        body: dict[str, Any] = {
            "counterfactual_id": str(counterfactual_id),
            "status": _status_from_rows(rows),
            "artifact_id": _first(rows, "input_artifact_id"),
            "artifact_summary": _capability(rows[0]).get("artifact_summary"),
            "completed_at": _completed_at(rows),
            "parity": _parity(by_mode),
        }
        for mode, row in sorted(by_mode.items()):
            body[mode.lower()] = _side_body(mode, row, held)
        body["delta"] = _delta(held)
        body["safety"] = _safety(by_mode, held)
        if held is None:
            body["output_retention"] = {
                "readings_available": False,
                "reason": (
                    "the readings of a counterfactual are not persisted: 14_PROMPTS.md "
                    "section 6.4 forbids storing either output for replay and forbids "
                    "caching them, and agent_runs carries no column for one. This process "
                    "did not run this counterfactual, so the two readings are gone; the "
                    "attribution, parity and safety blocks above are read from the rows."
                ),
            }
        return body

    # -- internals ---------------------------------------------------------

    async def _strategy(
        self, scope: OwnerScope, payload: Any, artifact_id: uuid.UUID
    ) -> tuple[str, str]:
        """Which MEMORY ON strategy actually ran, and why.

        Section 8.30: ``RERUN_SANDBOXED`` "exists only to show the reasoning
        path when no committed result is available". So a ``REPLAY_COMMITTED``
        request against an artifact with no committed draft runs sandboxed and
        **says so** -- ``CANONICAL_DECISIONS.md`` -> *Counterfactual parity
        canon* makes the header copy a function of ``memory_on.strategy``, so
        reporting the strategy that ran is the whole point of the field.
        """
        requested = getattr(payload, "memory_on_strategy", "REPLAY_COMMITTED") or "REPLAY_COMMITTED"
        if requested == "RERUN_SANDBOXED":
            return requested, "requested"
        if await self._store.has_committed_draft(scope, artifact_id):
            return "REPLAY_COMMITTED", "a committed draft exists for this artifact"
        return "RERUN_SANDBOXED", (
            "REPLAY_COMMITTED was requested and there is no committed Advocate draft for "
            "this artifact to replay; section 8.30 gives RERUN_SANDBOXED for exactly that "
            "case. The MEMORY ON column below ran just now"
        )

    async def _record_refusal(
        self,
        scope: OwnerScope,
        *,
        counterfactual_id: uuid.UUID,
        artifact: ArtifactFacts,
        case: CaseFacts | None,
        modes: Sequence[str],
        started_at: datetime,
        code: str,
        detail: str,
    ) -> dict[str, Any]:
        """Persist the attempt, then refuse with the reason (``D-00-005``).

        The rows are written even though nothing ran: an attempt that leaves no
        trace is indistinguishable from an attempt nobody made, and "the bytes
        for this artifact are not on this machine" is a fact a judge should be
        able to read out of the ledger rather than out of a log line.
        """
        for mode in modes:
            run_id = uuid.uuid4()
            await self._store.open_run(
                run_store.insert_params(
                    run_id=run_id,
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    trace_id=counterfactual_id,
                    graph_name=GRAPH_NAME_COUNTERFACTUAL,
                    graph_version=GRAPH_VERSION_COUNTERFACTUAL,
                    model_route=_model_route(),
                    memory_mode="ON" if mode == "MEMORY_ON" else "OFF",
                    started_at=started_at,
                    expires_at=started_at + _RUN_WINDOW,
                    artifact_id=artifact.artifact_id,
                    allowed_case_ids=([str(case.case_id)] if mode == "MEMORY_ON" and case else []),
                    corpus_size_visible=0,
                    capability_status=_capability_status(
                        mode=mode, visible=0, artifact=artifact, strategy="RERUN_SANDBOXED"
                    ),
                )
            )
            await self._store.settle_run(
                run_store.settle_params(
                    run_id=run_id,
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    status="FAILED",
                    error_code=code,
                    finished_at=self._clock(),
                    model_calls=[],
                    capability_status={
                        **_capability_status(
                            mode=mode, visible=0, artifact=artifact, strategy="RERUN_SANDBOXED"
                        ),
                        "cannot_run_reason": detail,
                    },
                )
            )
        return {
            "counterfactual_id": str(counterfactual_id),
            "status": "FAILED",
            "artifact_id": str(artifact.artifact_id),
            "poll_url": f"/v1/judge-mode/counterfactual/{counterfactual_id}",
            "suggested_interval_ms": 1000,
            "error": {"code": code, "message": detail},
        }

    def _remember(self, counterfactual_id: uuid.UUID, held: dict[str, Any]) -> None:
        self._results[counterfactual_id] = held
        while len(self._results) > _RESULT_BUFFER:
            self._results.pop(next(iter(self._results)))


# ---------------------------------------------------------------------------
# Pure assembly. Everything below is a function of what was measured.
# ---------------------------------------------------------------------------


def _walk(
    *,
    mode: str,
    artifact: ArtifactFacts,
    blocks: Sequence[ContentBlock],
    router: Any,
    renderer: Any,
    binder: Any,
) -> CounterfactualState:
    """One side, on a worker thread. The router's call is a blocking one."""
    state = initial_counterfactual_state(
        mode=mode,  # type: ignore[arg-type]
        artifact_id=artifact.artifact_id,
        artifact_sha256=artifact.content_sha256,
        blocks=blocks,
    )
    return run_counterfactual(
        state, CounterfactualDeps(router=router, renderer=renderer, memory=binder)
    )


def _requested_modes(payload: Any) -> tuple[str, ...]:
    requested = getattr(payload, "modes", None)
    if not requested:
        return MODES
    return tuple(mode for mode in MODES if mode in set(requested))


def _model_route() -> dict[str, Any]:
    """``agent_runs.model_route``. Tier R only: the graph has one model node."""
    from agents.runtime.model_router.router import route

    spec = route("draft_action")
    return {"tier_r": None, "tier_e": None, "node": spec.name, "prompt_version": PROMPT_VERSION}


def _capability_status(
    *,
    mode: str,
    visible: int,
    artifact: ArtifactFacts,
    strategy: str,
    state: CounterfactualState | None = None,
) -> dict[str, Any]:
    """What this run was and was not allowed to do, plus the two parity digests.

    ``proposal_tool_bound`` is ``false`` on **both** rows and is the server's
    statement rather than a caller's: ``ck_agent_runs_counterfactual_toolless``
    is a CHECK on it for the OFF row, and section 8.30 safety property 3 makes
    the ON row proposal-free as well. No capability row is minted for either
    run, so there is no id an external caller could present in the first place.

    ``artifact_sha256`` and ``decode_params_sha256`` are two of section 8.31's
    six parity fields and **the applied schema has no column for either**.
    Recording them here is the least-bad place -- this is the run's
    configuration -- and it is named as a schema gap rather than left to look
    like a design.
    """
    from agents.runtime.graphs.counterfactual_graph import decode_params_digest

    return {
        "proposal_tool_bound": False,
        "memory_mode": "ON" if mode == "MEMORY_ON" else "OFF",
        "retrieval_enabled": mode == "MEMORY_ON",
        "canonical_memory_enabled": mode == "MEMORY_ON",
        "corpus_size_visible": visible,
        "strategy": strategy if mode == "MEMORY_ON" else None,
        "artifact_summary": artifact.summary,
        "artifact_sha256": artifact.content_sha256,
        "decode_params_sha256": (
            state.decode_params_sha256 if state is not None else decode_params_digest(model_id="")
        ),
    }


def _code(state: CounterfactualState) -> str:
    return state.errors[0].code if state.errors else "COUNTERFACTUAL_NO_READING"


def _memory_on_context(
    *,
    case: CaseFacts | None,
    proof: Mapping[str, Any] | None,
    corpus_size: int,
    artifact: ArtifactFacts,
) -> dict[str, Any]:
    """The TRUSTED STRUCTURED CONTEXT for MEMORY ON.

    The same three keys the empty block carries, filled. A different shape
    between the sides would make the comparison a comparison of prompt
    structures, which is the accusation the parity block exists to defeat.
    """
    return {
        "state_proof": dict(proof) if proof is not None else None,
        "retrieval": {
            "corpus_size_visible": corpus_size,
            "evidence": [],
            "beliefs": list((proof or {}).get("beliefs", [])),
            "conflicts": list((proof or {}).get("conflicts", [])),
            "commitments": list((proof or {}).get("commitments", [])),
        },
        "case": (
            {"case_id": str(case.case_id), "title": case.title, "revision": case.revision}
            if case is not None
            else None
        ),
        "artifact_summary": artifact.summary,
    }


def _status(sides: Mapping[str, SideResult], modes: Sequence[str]) -> str:
    produced = [mode for mode in modes if sides.get(mode) and sides[mode].reading is not None]
    if len(produced) == len(modes) and produced:
        return "COMPLETED"
    if produced:
        return "PARTIAL"
    return "FAILED"


def _status_from_rows(rows: Sequence[Mapping[str, Any]]) -> str:
    statuses = {str(row["status"]) for row in rows}
    if statuses == {"SUCCEEDED"}:
        return "COMPLETED"
    if "SUCCEEDED" in statuses:
        return "PARTIAL"
    if "RUNNING" in statuses:
        return "RUNNING"
    return "FAILED"


def _first(rows: Sequence[Mapping[str, Any]], key: str) -> str | None:
    for row in rows:
        if row.get(key) is not None:
            return str(row[key])
    return None


def _completed_at(rows: Sequence[Mapping[str, Any]]) -> Any:
    finished = [row["finished_at"] for row in rows if row.get("finished_at") is not None]
    return max(finished) if finished else None


def _capability(row: Mapping[str, Any]) -> Mapping[str, Any]:
    status = row.get("capability_status")
    return status if isinstance(status, Mapping) else {}


def _calls(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    calls = row.get("model_calls")
    return list(calls) if isinstance(calls, list) else []


def _model_id(row: Mapping[str, Any]) -> str | None:
    calls = _calls(row)
    return str(calls[-1]["model_id"]) if calls else None


def _prompt_version(row: Mapping[str, Any]) -> str | None:
    calls = _calls(row)
    return str(calls[-1]["prompt_version"]) if calls else None


def _parity(by_mode: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Section 8.31's normative block, computed from the two persisted rows.

    Each field is read **separately from each row**. Both sides could be filled
    from one source and then compared with themselves -- which would report
    ``all_equal: true`` unconditionally and is precisely the decorative version
    the canon calls out.
    """
    off = by_mode.get("MEMORY_OFF", {})
    on = by_mode.get("MEMORY_ON", {})
    fields = {
        "artifact_id": (_maybe(off, "input_artifact_id"), _maybe(on, "input_artifact_id")),
        "artifact_sha256": (
            _capability(off).get("artifact_sha256"),
            _capability(on).get("artifact_sha256"),
        ),
        "model_id": (_model_id(off), _model_id(on)),
        "prompt_version": (_prompt_version(off), _prompt_version(on)),
        "graph_version": (_maybe(off, "graph_version"), _maybe(on, "graph_version")),
        "decode_params_sha256": (
            _capability(off).get("decode_params_sha256"),
            _capability(on).get("decode_params_sha256"),
        ),
    }
    block: dict[str, Any] = {}
    both_present = bool(off) and bool(on)
    for name, (off_value, on_value) in fields.items():
        equal = both_present and off_value is not None and off_value == on_value
        block[name] = {"off": off_value, "on": on_value, "equal": equal}
    block["all_equal"] = all(entry["equal"] for entry in block.values() if isinstance(entry, dict))
    return block


def _maybe(row: Mapping[str, Any], key: str) -> str | None:
    value = row.get(key)
    return None if value is None else str(value)


def _side_body(mode: str, row: Mapping[str, Any], held: Mapping[str, Any] | None) -> dict[str, Any]:
    capability = _capability(row)
    side: SideResult | None = None
    if held is not None:
        side = held["sides"].get(mode)
    body: dict[str, Any] = {
        "mode": mode,
        "retrieval_enabled": bool(capability.get("retrieval_enabled")),
        "canonical_memory_enabled": bool(capability.get("canonical_memory_enabled")),
        "corpus_size_visible": int(row.get("retrieval_candidate_count") or 0),
        "model_id": _model_id(row),
        "duration_ms": side.duration_ms if side is not None else None,
        "output": _output(side),
        "why": side.reading.why if side is not None and side.reading is not None else None,
    }
    if mode == "MEMORY_ON":
        body["strategy"] = capability.get("strategy")
        if held is not None:
            body["strategy_reason"] = held.get("strategy_reason")
            case = held.get("case")
            body["case_linked"] = (
                {
                    "case_id": str(case.case_id),
                    "title": case.title,
                    "revision_before": held.get("revision_before"),
                    "revision_after": held.get("revision_after"),
                }
                if case is not None
                else None
            )
    if str(row.get("status")) in {"FAILED", "ABANDONED"}:
        body["error"] = {
            "code": row.get("error_code"),
            "message": capability.get("cannot_run_reason"),
        }
    return body


def _output(side: SideResult | None) -> dict[str, Any] | None:
    if side is None or side.reading is None:
        return None
    reading = side.reading
    return {
        "headline": reading.headline,
        "classification": reading.classification,
        "conflicts_detected": reading.conflicts_detected,
        "recommended_action": reading.recommended_action,
        "draft_text": reading.draft_text or None,
        "support_ids": list(reading.support_ids),
        "omitted_because_unsupported": list(reading.omitted_because_unsupported),
    }


def _delta(held: Mapping[str, Any] | None) -> dict[str, Any]:
    """The comparison, and it is arithmetic over the two readings.

    ``verdict`` is a sentence about what was measured, not a stored line: with
    no readings held it says so instead of repeating the specification's
    example, which would be the scripted animation *Judge Mode* forbids.
    """
    if held is None:
        return {
            "verdict": (
                "the two readings are not retained, so no delta can be computed; "
                "see output_retention"
            )
        }
    off = held["sides"].get("MEMORY_OFF")
    on = held["sides"].get("MEMORY_ON")
    off_reading = off.reading if off is not None else None
    on_reading = on.reading if on is not None else None
    if off_reading is None or on_reading is None:
        return {"verdict": "one side produced no reading; there is nothing to compare"}
    return {
        "conflicts_detected": {
            "off": off_reading.conflicts_detected,
            "on": on_reading.conflicts_detected,
        },
        "actions_recommended": {
            "off": int(off_reading.recommended_action != "NONE"),
            "on": int(on_reading.recommended_action != "NONE"),
        },
        "support_ids_cited": {
            "off": len(off_reading.support_ids),
            "on": len(on_reading.support_ids),
        },
        "corpus_size_visible": {
            "off": off.corpus_size_visible if off else 0,
            "on": on.corpus_size_visible if on else 0,
        },
        "verdict": (
            f"memory off classified it {off_reading.classification} and recommended "
            f"{off_reading.recommended_action}; memory on classified it "
            f"{on_reading.classification} and recommended {on_reading.recommended_action}"
        ),
    }


def _safety(
    by_mode: Mapping[str, Mapping[str, Any]], held: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Section 8.31's safety block. Three of the four are measurements.

    ``case_revision_changed_by_counterfactual`` compares the revision read
    before the runs with the revision read after them. It is not a constant and
    it is not derived from the absence of a write path: a counterfactual that
    somehow moved the record must say so on the same screen that shows the
    comparison.
    """
    off = by_mode.get("MEMORY_OFF", {})
    proposal_tool = bool(_capability(off).get("proposal_tool_bound"))
    block: dict[str, Any] = {
        "memory_off_had_proposal_tool": proposal_tool,
        "memory_off_wrote_canonical_state": False,
        "memory_off_admitted_evidence": False,
        "case_revision_changed_by_counterfactual": False,
    }
    if held is None:
        block["measured"] = False
        block["note"] = (
            "the before/after revision and evidence counts were measured by the process "
            "that ran this counterfactual and are not persisted; the flags above are the "
            "structural facts the rows carry, not this run's measurement"
        )
        return block
    before, after = held.get("revision_before"), held.get("revision_after")
    evidence_before, evidence_after = held.get("evidence_before"), held.get("evidence_after")
    block["measured"] = True
    block["case_revision_before"] = before
    block["case_revision_after"] = after
    block["case_revision_changed_by_counterfactual"] = before != after
    block["memory_off_admitted_evidence"] = evidence_before != evidence_after
    block["memory_off_wrote_canonical_state"] = before != after or evidence_before != evidence_after
    return block
