"""``T9.4``-``T9.6`` -- revalidation, the allowlist, the attempt ledger, idempotency.

The five gates, five assertions
--------------------------------
``T9.6``, first sub-task: "Enumerate every path that could produce an external
effect and assert each one refuses without a committed basis, a fresh revision,
a matching hash, an allowlisted recipient, and a human approval."

- no committed basis -> :func:`test_an_uncommitted_basis_refuses_and_sends_nothing`
- stale revision     -> :func:`test_a_moved_case_revision_aborts_the_send` (``G9.1``)
- changed draft hash -> :func:`test_an_edited_draft_aborts_the_send` (``G9.2``)
- recipient          -> :func:`test_a_recipient_off_the_allowlist_aborts_the_send` (``G9.5``)
- human approval     -> :func:`test_an_unapproved_intent_cannot_execute`

The revision and the hash are asserted **separately and independently**: each
test moves exactly one of the two and leaves the other untouched. A single test
that changed both would pass against an implementation that only ever checked
one of them, and the difference between those two implementations is the
difference between "a human approved this" and "a human approved something that
looked like this".

Every refusal assertion reads the sink's own call log
------------------------------------------------------
``G9.1`` is explicit: "**provider calls made: 0**, asserted against the sink's
call log rather than a mock counter." :class:`DemoSink` records every
invocation, so "nothing was sent" is a property of the thing that would have
sent it rather than of a spy the test wired up.
"""

from __future__ import annotations

import pytest

from services.control_plane.app.actions import drafts, executor, intents
from services.control_plane.app.actions.policy import ActionPolicy
from services.control_plane.app.actions.sink import ActionSink, DemoSink, SinkMessage, SinkReceipt
from services.control_plane.app.actions.store import ActionScope

pytestmark = pytest.mark.unit


def _scope(hero) -> ActionScope:
    return ActionScope(tenant_id=hero.tenant_id, user_id=hero.user_id)


async def _approved(memory_store, snapshot, hero, make_draft, policy, clock):
    """An intent that a human has approved, and nothing else has happened yet."""
    memory_store.put_snapshot(_scope(hero), snapshot)
    service = intents.ActionIntentService(store=memory_store, policy=policy, clock=clock)
    created = await service.create(
        _scope(hero),
        intents.CreateIntentRequest(
            case_id=hero.case_id,
            action_type="OUTBOUND_EMAIL_DISPUTE",
            recipient=hero.recipient,
            draft=make_draft(),
            rationale="A counterparty claim asserts billable service in a terminated period.",
            supporting_belief_versions=(hero.belief_version_id,),
            basis_case_revision=hero.basis_case_revision,
            idempotency_key="0" * 64,
            created_by_agent_run_id=hero.agent_run_id,
        ),
    )
    record = await service.approve(
        _scope(hero),
        created.intent.id,
        intents.ApproveRequest(
            approved_draft={"subject": "Disputed invoice 88431", "body": "Hello,\n\nAlex Rivera"},
            client_case_revision=hero.basis_case_revision,
            approved_by_user_id=hero.user_id,
        ),
    )
    return record.intent


def _executor(memory_store, sink, policy, clock) -> executor.ActionExecutor:
    return executor.ActionExecutor(store=memory_store, sink=sink, policy=policy, clock=clock)


# ==========================================================================
# The happy path -- step 13 of the hero flow
# ==========================================================================


async def test_an_approved_intent_executes_and_is_recorded(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """One send, one ledger row, one correlation id.

    The correlation id is the difference between "we logged that we sent it"
    and "the provider has a record of it". It is stored, so an outcome is
    traceable to a provider record rather than to a log line.
    """
    sink = DemoSink()
    intent = await _approved(memory_store, snapshot, hero, make_draft, open_policy, clock)

    outcome = await _executor(memory_store, sink, open_policy, clock).execute(
        _scope(hero), intent.id, idempotency_key=intent.idempotency_key
    )

    assert outcome.status == "EXECUTED"
    assert outcome.attempt_no == 1
    assert outcome.provider_correlation_id is not None
    assert outcome.blocking_reasons == ()
    assert len(sink.messages) == 1
    assert sink.messages[0].subject == "Disputed invoice 88431"

    execution = memory_store.executions[0]
    assert execution.status == "SUCCEEDED"
    assert execution.revalidated_case_revision == hero.basis_case_revision
    assert execution.provider_correlation_id == outcome.provider_correlation_id
    assert execution.finished_at is not None
    assert len(execution.request_sha256) == 32
    reloaded = await memory_store.load_intent(_scope(hero), intent.id)
    assert reloaded is not None and reloaded.status == "EXECUTED"


async def test_the_message_is_delivered_to_the_demo_sink_not_the_counterparty(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """``ACTION_RECIPIENT_MODE`` defaults to ``DEMO_SINK``.

    The allowlist decides whether the counterparty *may* be written to; the
    recipient mode decides where the bytes actually go. In the demo they go to
    the sink, and the assertion is on the address the sink was handed.
    """
    sink = DemoSink()
    intent = await _approved(memory_store, snapshot, hero, make_draft, open_policy, clock)

    await _executor(memory_store, sink, open_policy, clock).execute(
        _scope(hero), intent.id, idempotency_key=intent.idempotency_key
    )

    assert sink.messages[0].recipient == "billing@demo-sink.provenance.app"


# ==========================================================================
# G9.1 -- the revision axis, alone
# ==========================================================================


async def test_a_moved_case_revision_aborts_the_send(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """``G9.1`` verbatim: approve at 13, commit an unrelated change to 14, execute.

    The draft is untouched: its digest still equals ``approval_draft_sha256``.
    The **only** thing that moved is the case revision, so a green result here
    can only mean the revision binding was checked.
    """
    sink = DemoSink()
    intent = await _approved(memory_store, snapshot, hero, make_draft, open_policy, clock)
    assert drafts.draft_digest(intent.draft_payload) == intent.approval_draft_sha256

    memory_store.advance_case_revision(_scope(hero), hero.case_id, to=14)

    outcome = await _executor(memory_store, sink, open_policy, clock).execute(
        _scope(hero), intent.id, idempotency_key=intent.idempotency_key
    )

    assert outcome.status == "ABORTED_STALE"
    assert outcome.error_code == executor.CASE_REVISION_MOVED
    assert sink.calls == ()
    assert sink.messages == ()

    execution = memory_store.executions[0]
    assert execution.status == "ABORTED_STALE"
    assert execution.error_code == executor.CASE_REVISION_MOVED
    assert execution.revalidated_case_revision == 14
    assert execution.provider_correlation_id is None


async def test_the_expected_revision_in_the_request_is_checked_too(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Section 9.11: ``cases.revision == request.expected_case_revision``.

    The caller states what it believes the world looks like. A caller that
    believes something different from the database is a caller acting on a
    reading that has since expired.
    """
    sink = DemoSink()
    intent = await _approved(memory_store, snapshot, hero, make_draft, open_policy, clock)

    outcome = await _executor(memory_store, sink, open_policy, clock).execute(
        _scope(hero),
        intent.id,
        idempotency_key=intent.idempotency_key,
        expected_case_revision=99,
    )

    assert outcome.status == "ABORTED_STALE"
    assert outcome.error_code == executor.CASE_REVISION_MOVED
    assert sink.calls == ()


# ==========================================================================
# G9.2 -- the draft-hash axis, alone
# ==========================================================================


async def test_an_edited_draft_aborts_the_send(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """``G9.2``: the approval is bound to the exact bytes, not to the row.

    The case revision is **not** touched here, so this cannot pass by accident
    on an implementation that only checks the revision. One character of the
    body changes, and the approval stops applying to it.
    """
    sink = DemoSink()
    intent = await _approved(memory_store, snapshot, hero, make_draft, open_policy, clock)
    edited = dict(intent.draft_payload)
    edited["body"] = str(edited["body"]) + " P.S. I expect compensation."
    memory_store.tamper_draft_payload(_scope(hero), intent.id, edited)

    outcome = await _executor(memory_store, sink, open_policy, clock).execute(
        _scope(hero), intent.id, idempotency_key=intent.idempotency_key
    )

    assert outcome.status == "ABORTED_STALE"
    assert outcome.error_code == executor.DRAFT_HASH_CHANGED
    assert outcome.blocking_reasons == (executor.DRAFT_HASH_CHANGED,)
    assert sink.calls == ()
    assert memory_store.executions[0].revalidated_case_revision == hero.basis_case_revision


async def test_the_expected_draft_hash_in_the_request_is_checked_too(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Section 9.11: ``sha256(JCS(draft_payload)) == request.expected_draft_sha256``."""
    sink = DemoSink()
    intent = await _approved(memory_store, snapshot, hero, make_draft, open_policy, clock)

    outcome = await _executor(memory_store, sink, open_policy, clock).execute(
        _scope(hero),
        intent.id,
        idempotency_key=intent.idempotency_key,
        expected_draft_sha256=bytes(32),
    )

    assert outcome.status == "ABORTED_STALE"
    assert outcome.error_code == executor.DRAFT_HASH_CHANGED
    assert sink.calls == ()


# ==========================================================================
# G9.5 -- the allowlist, and the human-approval gate
# ==========================================================================


async def test_a_recipient_off_the_allowlist_aborts_the_send(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """``G9.5``: ``RECIPIENT_NOT_ALLOWLISTED``, zero provider calls.

    The allowlist is re-read at execution time. An operator who narrows it
    after an approval was recorded has narrowed it for messages that have not
    yet left, which is the only reading under which narrowing it is useful.
    """
    sink = DemoSink()
    intent = await _approved(memory_store, snapshot, hero, make_draft, open_policy, clock)
    closed = ActionPolicy(
        allowlist=frozenset(),
        recipient_mode="DEMO_SINK",
        demo_sink_domain="demo-sink.provenance.app",
    )

    outcome = await _executor(memory_store, sink, closed, clock).execute(
        _scope(hero), intent.id, idempotency_key=intent.idempotency_key
    )

    assert outcome.status == "ABORTED_STALE"
    assert outcome.error_code == executor.RECIPIENT_NOT_ALLOWLISTED
    assert sink.calls == ()


async def test_an_unapproved_intent_cannot_execute(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """The human click is a gate, not a formality.

    ``ck_action_intents_execution_needs_approval`` makes the *database* state
    unrepresentable; this is the same rule in application code, so the failure
    is legible and still impossible.
    """
    sink = DemoSink()
    memory_store.put_snapshot(_scope(hero), snapshot)
    service = intents.ActionIntentService(store=memory_store, policy=open_policy, clock=clock)
    created = await service.create(
        _scope(hero),
        intents.CreateIntentRequest(
            case_id=hero.case_id,
            action_type="OUTBOUND_EMAIL_DISPUTE",
            recipient=hero.recipient,
            draft=make_draft(),
            rationale="r",
            supporting_belief_versions=(hero.belief_version_id,),
            basis_case_revision=hero.basis_case_revision,
            idempotency_key="0" * 64,
        ),
    )

    outcome = await _executor(memory_store, sink, open_policy, clock).execute(
        _scope(hero), created.intent.id, idempotency_key=created.intent.idempotency_key
    )

    assert outcome.status == "ABORTED_STALE"
    assert outcome.error_code == executor.NOT_APPROVED
    assert sink.calls == ()


async def test_an_uncommitted_basis_refuses_and_sends_nothing(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Invariant 4 at the executor: no external effect from uncommitted state.

    The approval was recorded while the case had a committed basis; the basis
    is then withdrawn. Nothing may leave.
    """
    sink = DemoSink()
    intent = await _approved(memory_store, snapshot, hero, make_draft, open_policy, clock)
    memory_store.withdraw_committed_basis(_scope(hero), hero.case_id)

    outcome = await _executor(memory_store, sink, open_policy, clock).execute(
        _scope(hero), intent.id, idempotency_key=intent.idempotency_key
    )

    assert outcome.status == "ABORTED_STALE"
    assert outcome.error_code == executor.NO_COMMITTED_BASIS
    assert sink.calls == ()


async def test_a_superseded_supporting_belief_aborts_the_send(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Section 9.11: every ``supporting_belief_versions[i]`` is still current."""
    sink = DemoSink()
    intent = await _approved(memory_store, snapshot, hero, make_draft, open_policy, clock)
    memory_store.supersede_belief_versions(_scope(hero), hero.case_id, frozenset())

    outcome = await _executor(memory_store, sink, open_policy, clock).execute(
        _scope(hero), intent.id, idempotency_key=intent.idempotency_key
    )

    assert outcome.status == "ABORTED_STALE"
    assert outcome.error_code == executor.SUPPORT_BELIEF_SUPERSEDED
    assert sink.calls == ()


# ==========================================================================
# G9.4 -- idempotency at the provider boundary
# ==========================================================================


async def test_two_executes_under_one_key_produce_exactly_one_message(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """``G9.4`` verbatim, in the order the gate states it.

    "the key is asserted equal across attempts as a string before the
    single-effect assertion is made" -- because a test that sends twice under
    two different keys and finds one message has proven nothing about
    idempotency.
    """
    sink = DemoSink()
    intent = await _approved(memory_store, snapshot, hero, make_draft, open_policy, clock)
    runner = _executor(memory_store, sink, open_policy, clock)

    first = await runner.execute(_scope(hero), intent.id, idempotency_key=intent.idempotency_key)
    second = await runner.execute(_scope(hero), intent.id, idempotency_key=intent.idempotency_key)

    assert first.idempotency_key == second.idempotency_key == intent.idempotency_key
    assert len(sink.messages) == 1
    assert second.replayed is True
    assert second.status == first.status == "EXECUTED"
    assert second.provider_correlation_id == first.provider_correlation_id
    assert second.action_execution_id == first.action_execution_id


async def test_the_execution_key_is_the_intents_key_and_never_a_fresh_uuid(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """``T9.5``: derive the key from the intent, never from a fresh UUID.

    Section 9.11 states the same rule from the other side: "The key **must**
    equal ``action_intents.idempotency_key``." A mismatched key is refused
    rather than tolerated, because tolerating it would make the key advisory.
    """
    sink = DemoSink()
    intent = await _approved(memory_store, snapshot, hero, make_draft, open_policy, clock)

    with pytest.raises(intents.ActionRefusedError) as raised:
        await _executor(memory_store, sink, open_policy, clock).execute(
            _scope(hero), intent.id, idempotency_key="f" * 64
        )

    assert raised.value.reason_code == intents.IDEMPOTENCY_CONFLICT
    assert sink.calls == ()


async def test_a_provider_side_duplicate_is_success_with_the_original_correlation_id(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """``T9.5``, third sub-task. The sink is the authority on what it already sent.

    The executor's own pre-check is bypassed here to reach the case it exists
    to make unreachable: two executors racing. The sink recognises the key,
    returns the first correlation id, and the message count stays at one.
    """
    sink = DemoSink()
    intent = await _approved(memory_store, snapshot, hero, make_draft, open_policy, clock)
    runner = _executor(memory_store, sink, open_policy, clock)
    first = await runner.execute(_scope(hero), intent.id, idempotency_key=intent.idempotency_key)

    memory_store.forget_executions()
    await memory_store.set_status(_scope(hero), intent.id, status="APPROVED")
    second = await runner.execute(_scope(hero), intent.id, idempotency_key=intent.idempotency_key)

    assert second.status == "EXECUTED"
    assert second.provider_correlation_id == first.provider_correlation_id
    assert len(sink.calls) == 2
    assert len(sink.messages) == 1


async def test_a_retryable_failure_records_attempt_two(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Attempts are free; success is once.

    ``uq_action_executions_single_success`` is ``UNIQUE (action_intent_id)
    WHERE status = 'SUCCEEDED'`` rather than ``UNIQUE (action_intent_id)``
    precisely so a transient provider error does not become permanent. Both
    attempts are on the ledger and only the second one sent anything.
    """
    sink = _FlakySink(fail_first=1)
    intent = await _approved(memory_store, snapshot, hero, make_draft, open_policy, clock)
    runner = _executor(memory_store, sink, open_policy, clock)

    first = await runner.execute(_scope(hero), intent.id, idempotency_key=intent.idempotency_key)
    assert first.status == "FAILED_RETRYABLE"
    assert first.error_code == "PROVIDER_TRANSIENT"

    await memory_store.set_status(_scope(hero), intent.id, status="APPROVED")
    second = await runner.execute(_scope(hero), intent.id, idempotency_key=intent.idempotency_key)

    assert [row.attempt_no for row in memory_store.executions] == [1, 2]
    assert [row.status for row in memory_store.executions] == ["FAILED_RETRYABLE", "SUCCEEDED"]
    assert second.status == "EXECUTED"
    assert len(sink.sent) == 1


# ==========================================================================
# G9.6 -- the kill switch
# ==========================================================================


async def test_the_kill_switch_records_the_approval_and_sends_nothing(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """``G9.6``: a zero-length sink call log with a non-zero approval count.

    The rollback position in ``ops/gates/PHASE_09.md`` is exactly this: roll
    back to ``G-8`` and set ``PV_ACTION_EXECUTION_MODE=DISABLED``. Approvals
    continue to be recorded. It is tested at the gate rather than discovered at
    the demo.
    """
    sink = DemoSink()
    disabled = ActionPolicy(
        allowlist=frozenset({hero.recipient}),
        execution_mode="DISABLED",
        recipient_mode="DEMO_SINK",
        demo_sink_domain="demo-sink.provenance.app",
    )
    intent = await _approved(memory_store, snapshot, hero, make_draft, disabled, clock)

    outcome = await _executor(memory_store, sink, disabled, clock).execute(
        _scope(hero), intent.id, idempotency_key=intent.idempotency_key
    )

    approvals = [row for row in memory_store.intents if row.approval_draft_sha256 is not None]
    assert len(approvals) == 1
    assert sink.calls == ()
    assert outcome.status == "NOT_EXECUTED"
    assert outcome.error_code == executor.ACTION_EXECUTION_DISABLED
    assert memory_store.executions == ()
    reloaded = await memory_store.load_intent(_scope(hero), intent.id)
    assert reloaded is not None and reloaded.status == "APPROVED"


# ==========================================================================
# G9.7 -- the sabotage hook is reachable and load-bearing
# ==========================================================================


def test_revalidate_revision_is_a_predicate_that_fails_closed(hero) -> None:
    """The ``PV_SABOTAGE`` symbol, asserted directly.

    It is a **predicate** rather than a reason-code builder, and the reason is
    the one ``tests/sabotage_matrix.yaml`` records for
    ``provenance_db.retry.is_retryable``: ``PV_SABOTAGE`` replaces the symbol
    with the identity function, and an identity function returns its truthy
    argument. A predicate therefore degrades to "yes, still valid" under
    sabotage -- the unsafe direction -- so ``G9.1`` goes red rather than green.
    """
    fresh = executor.RevisionCheck(
        basis_case_revision=13, current_case_revision=13, expected_case_revision=13
    )
    moved = executor.RevisionCheck(
        basis_case_revision=13, current_case_revision=14, expected_case_revision=None
    )

    assert executor.revalidate_revision(fresh) is True
    assert executor.revalidate_revision(moved) is False
    assert executor.SABOTAGE_MODULE == "actions.executor"
    assert "revalidate_revision" in executor.SABOTAGE_HOOKS


def test_revalidate_reaches_the_hook_through_the_module_global() -> None:
    """A ``from``-import would copy the reference before ``PV_SABOTAGE`` rebinds it.

    Checked against the source rather than trusted, for the same reason
    ``test_derive_outstanding_calls_money_outstanding_through_the_module_global``
    exists: an entry in the sabotage matrix that the sabotage cannot reach
    reports green forever.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(executor.revalidate))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "revalidate_revision" in called


# ==========================================================================
# Test doubles
# ==========================================================================


class _FlakySink:
    """A sink whose first *n* sends fail retryably. Not a mock: it keeps a log."""

    provider = "SIMULATOR"

    def __init__(self, *, fail_first: int) -> None:
        self._remaining_failures = fail_first
        self.calls: list[SinkMessage] = []
        self.sent: list[SinkMessage] = []

    async def send(self, message: SinkMessage) -> SinkReceipt:
        self.calls.append(message)
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise executor.ProviderTransientError("PROVIDER_TRANSIENT")
        self.sent.append(message)
        return SinkReceipt(provider=self.provider, provider_correlation_id="sim-0001")


def test_the_demo_sink_satisfies_the_action_sink_protocol() -> None:
    """The executor is written against the protocol, not against ``DemoSink``.

    SES is gone with the pivot and the real transport is a later wiring
    decision. What the executor must not acquire in the meantime is a
    dependency on the stand-in.
    """
    assert isinstance(DemoSink(), ActionSink)
    assert isinstance(_FlakySink(fail_first=0), ActionSink)


# ==========================================================================
# When the attempt was recorded -- section 9.11's `executed_at`
# ==========================================================================


async def test_the_outcome_carries_the_ledger_timestamp_not_the_callers_clock(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """Section 9.11's response body has ``executed_at``; this is where it comes from.

    The value is the ``action_executions.finished_at`` the store wrote, read
    back off the row -- never a timestamp taken by whoever is rendering the
    response. Those are different facts: one is when the attempt was recorded,
    the other is when an adapter observed it, and only the first is defensible
    as an audit record.
    """
    sink = DemoSink()
    intent = await _approved(memory_store, snapshot, hero, make_draft, open_policy, clock)

    outcome = await _executor(memory_store, sink, open_policy, clock).execute(
        _scope(hero), intent.id, idempotency_key=intent.idempotency_key
    )

    assert outcome.finished_at == memory_store.executions[0].finished_at
    assert outcome.finished_at == hero.now


async def test_a_replayed_outcome_reports_the_first_attempts_timestamp(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """The replay answers *when it was sent*, not *when it was asked about*.

    A second execute under one key returns the first attempt's outcome, and the
    timestamp is part of that outcome. Re-stamping it would make the audit
    record say the message went out twice.
    """
    sink = DemoSink()
    intent = await _approved(memory_store, snapshot, hero, make_draft, open_policy, clock)
    runner = _executor(memory_store, sink, open_policy, clock)

    first = await runner.execute(_scope(hero), intent.id, idempotency_key=intent.idempotency_key)
    second = await runner.execute(_scope(hero), intent.id, idempotency_key=intent.idempotency_key)

    assert second.replayed is True
    assert second.finished_at == first.finished_at


async def test_a_refusal_carries_the_timestamp_of_the_refusal(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """An ``ABORTED_STALE`` attempt is finished the moment it is written.

    ``ck_action_executions_terminal`` requires a ``finished_at`` on anything
    that is not ``STARTED``, so the row has one; surfacing it means "we refused
    to send this, at this time" is answerable from the outcome alone.
    """
    sink = DemoSink()
    intent = await _approved(memory_store, snapshot, hero, make_draft, open_policy, clock)
    memory_store.advance_case_revision(_scope(hero), hero.case_id, to=14)

    outcome = await _executor(memory_store, sink, open_policy, clock).execute(
        _scope(hero), intent.id, idempotency_key=intent.idempotency_key
    )

    assert outcome.status == "ABORTED_STALE"
    assert outcome.finished_at == memory_store.executions[0].finished_at


async def test_the_kill_switch_outcome_has_no_timestamp_to_report(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """No attempt was recorded, so there is no moment to name.

    ``None`` here is the honest answer and not a missing value: reporting a
    time would claim an attempt exists on the ledger. The same reasoning as
    ``action_execution_id`` and ``attempt_no``, which are already ``None`` on
    this path.
    """
    sink = DemoSink()
    disabled = ActionPolicy(
        allowlist=frozenset({hero.recipient}),
        execution_mode="DISABLED",
        recipient_mode="DEMO_SINK",
        demo_sink_domain="demo-sink.provenance.app",
    )
    intent = await _approved(memory_store, snapshot, hero, make_draft, disabled, clock)

    outcome = await _executor(memory_store, sink, disabled, clock).execute(
        _scope(hero), intent.id, idempotency_key=intent.idempotency_key
    )

    assert outcome.status == "NOT_EXECUTED"
    assert outcome.finished_at is None
    assert outcome.action_execution_id is None


# ==========================================================================
# The execution key comes off the row, and a mint is not a substitute
# ==========================================================================


async def test_the_mint_is_not_a_valid_source_for_the_execution_key(
    memory_store, snapshot, hero, make_draft, open_policy, clock
) -> None:
    """A caller that re-derives the key instead of reading it 409s every send.

    ``T9.5`` says derive the execution key from the intent and section 9.11
    says it MUST equal ``action_intents.idempotency_key``. Those are the same
    instruction, and :func:`drafts.mint_idempotency_key` looks like it
    satisfies both -- it is deterministic, intent-scoped, and never a fresh
    UUID. It does not, for two independent reasons:

    1. Section 9.8 step 7 stores the **request** key when the Advocate supplied
       one, so the row's key was never the mint to begin with.
    2. The mint covers ``draft_sha256``, and approving with any edit moves that
       digest -- so even an intent created from a mint stops matching its own
       recomputation the moment a human changes a word.

    Either condition alone turns every execution of a legitimately approved
    intent into a ``409``. This test pins the divergence so the trap is visible
    here rather than in a demo, and asserts the executor refuses rather than
    tolerating it: a tolerant executor would send under a key that collides
    with nothing, which is the second send an advisory key invites.
    """
    sink = DemoSink()
    intent = await _approved(memory_store, snapshot, hero, make_draft, open_policy, clock)
    minted = drafts.mint_idempotency_key(
        tenant_id=hero.tenant_id,
        user_id=hero.user_id,
        case_id=hero.case_id,
        action_type=intent.action_type,
        draft_sha256=intent.draft_sha256,
    )

    assert minted != intent.idempotency_key

    with pytest.raises(intents.ActionRefusedError) as raised:
        await _executor(memory_store, sink, open_policy, clock).execute(
            _scope(hero), intent.id, idempotency_key=minted
        )

    assert raised.value.reason_code == intents.IDEMPOTENCY_CONFLICT
    assert sink.calls == ()
