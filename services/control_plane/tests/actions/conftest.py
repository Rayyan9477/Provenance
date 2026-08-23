"""Fixtures for the Phase 9 action lane (``T9.1``-``T9.6``).

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Memory, action, and time*, row **External
  action**: draft, validate grounding, create intent, human approve, bind
  approval to case revision **and** draft SHA-256, revalidate, execute
  idempotently.
- ``docs/EXECUTION/70_TASK_PLAN.md`` section 12, ``T9.1``-``T9.6``.
- ``docs/specs/15_API_SPEC.md`` sections 8.23-8.27, 9.8 and 9.11.
- ``db/migrations/versions/0007_action_plane.py`` -- the real column names.

Every test in this directory carries ``pytest.mark.unit``: the root
``conftest.py`` then unsets the five credential variables and refuses every
outbound socket reach. The action plane must be fully decidable without a
network, because the one thing it can do that cannot be undone is reach one.

The database half of this phase lives in
``services/control_plane/tests/db/test_action_plane.py`` and carries the ``db``
marker: schema CHECKs and the partial UNIQUE that make idempotency a database
guarantee rather than an application one.

Why every shared value hangs off ``Hero``
-----------------------------------------
``pyproject.toml`` sets ``--import-mode=importlib`` and ``.ruff.toml`` bans
relative imports, so a test module cannot import this file. The kernel suite
solved the same problem the same way and the reason is recorded there: the
shared vocabulary travels on one frozen object handed over by a fixture, which
keeps it in exactly one place instead of two that drift.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import pytest

from provenance_contracts.actions import DraftAction
from provenance_contracts.resolution import ModelAttribution
from provenance_domain.enums import ModelTier
from services.control_plane.app.actions import drafts, store
from services.control_plane.app.actions.policy import ActionPolicy
from services.control_plane.app.actions.support_validation import GroundingSnapshot

pytestmark = pytest.mark.unit

__all__ = ["DraftFactory", "Hero"]

#: ``20_TDD_STRATEGY.md`` section 4.2 rule 3: no wall-clock read in a test.
_NOW: Final[datetime] = datetime(2026, 9, 18, 13, 0, 0, tzinfo=UTC)

#: A factual sentence the committed record actually supports.
_GROUNDED_SENTENCE: Final[str] = (
    "On 15 May 2026 your billing team confirmed in writing that my cancellation "
    "was processed and that service would end on 31 May 2026."
)
_GROUNDED_BODY: Final[str] = (
    "Hello,\n\n" + _GROUNDED_SENTENCE + "\n\nPlease confirm the account is closed.\n\nAlex Rivera"
)

#: ``T9.1``'s acceptance sentence: a confirmation on a date for which no
#: evidence exists anywhere in the committed record.
_UNGROUNDED_SENTENCE: Final[str] = "You confirmed cancellation on 20 May 2026."

DraftFactory = Callable[..., DraftAction]

# The seeded identities, hoisted to module level so the frozen dataclass below
# has plain names as defaults. A dataclass default that is a *call* is
# evaluated once at class-definition time, which is fine for a UUID and a trap
# for anything mutable; ruff's RUF009 refuses to distinguish the two cases, and
# it is right not to. The kernel suite hoists for the same reason.
_TENANT: Final = uuid.UUID("33333333-3333-4333-8333-333333333333")
_USER: Final = uuid.UUID("44444444-4444-4444-8444-444444444444")
_OTHER_USER: Final = uuid.UUID("44444444-4444-4444-8444-000000000099")
_CASE: Final = uuid.UUID("22222222-2222-4222-8222-222222222222")
_OTHER_CASE: Final = uuid.UUID("22222222-2222-4222-8222-000000000099")
_AGENT_RUN: Final = uuid.UUID("99999999-9999-4999-8999-999999999999")
_INTENT: Final = uuid.UUID("018f9c2f-1111-7abc-8def-000000000001")
_BELIEF_VERSION: Final = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000002")
_TERMINATION_EVIDENCE: Final = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000001")
_INVOICE_EVIDENCE: Final = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")
_PHANTOM_SUPPORT: Final = uuid.UUID("deadbeef-0000-4000-8000-000000000001")
_DRAFT: Final = uuid.UUID("77777777-0000-4000-8000-000000000001")


@dataclass(frozen=True, slots=True)
class Hero:
    """The seeded identities and the shared draft vocabulary, in one object.

    Every id is a literal. A ``uuid4()`` in a fixture would make the draft-hash
    assertions fail for a reason that has nothing to do with the property they
    assert -- and, worse, would make them pass for the wrong reason if the hash
    ever stopped covering ids.
    """

    tenant_id: uuid.UUID = _TENANT
    user_id: uuid.UUID = _USER
    other_user_id: uuid.UUID = _OTHER_USER
    case_id: uuid.UUID = _CASE
    other_case_id: uuid.UUID = _OTHER_CASE
    agent_run_id: uuid.UUID = _AGENT_RUN
    intent_id: uuid.UUID = _INTENT
    draft_id: uuid.UUID = _DRAFT
    # Support ids the State Proof actually carries.
    belief_version_id: uuid.UUID = _BELIEF_VERSION
    termination_evidence_id: uuid.UUID = _TERMINATION_EVIDENCE
    invoice_evidence_id: uuid.UUID = _INVOICE_EVIDENCE
    # An id no proof carries. Citing it is the ungrounded case.
    phantom_support_id: uuid.UUID = _PHANTOM_SUPPORT
    recipient: str = "billing@northlinefiber.example"
    off_allowlist_recipient: str = "legal@some-other-counterparty.example"
    basis_case_revision: int = 13
    now: datetime = _NOW
    grounded_sentence: str = _GROUNDED_SENTENCE
    grounded_body: str = _GROUNDED_BODY
    ungrounded_sentence: str = _UNGROUNDED_SENTENCE


@pytest.fixture
def hero() -> Hero:
    return Hero()


@pytest.fixture
def snapshot(hero: Hero) -> GroundingSnapshot:
    """The committed State Proof, as a grounding snapshot at revision 13."""
    return GroundingSnapshot(
        case_id=hero.case_id,
        case_revision=hero.basis_case_revision,
        support_ids=frozenset(
            {
                hero.belief_version_id,
                hero.termination_evidence_id,
                hero.invoice_evidence_id,
            }
        ),
        current_belief_version_ids=frozenset({hero.belief_version_id}),
        has_committed_kernel_decision=True,
    )


def _attribution() -> ModelAttribution:
    return ModelAttribution(
        provider="gemini",
        model_id="gemini-3.7-flash",
        tier=ModelTier.R,
        prompt_version="pv-draft-1.0.0",
        graph_name="advocate_graph",
        graph_version="1.0.0",
    )


@pytest.fixture
def make_draft(hero: Hero) -> DraftFactory:
    """A grounded ``DraftAction``; keyword overrides change one thing at a time."""

    def _make(**overrides: Any) -> DraftAction:
        support = overrides.pop("support_ids", (hero.belief_version_id,))
        body = overrides.pop("body", hero.grounded_body)
        sentence = overrides.pop("sentence", hero.grounded_sentence)
        if "claims" in overrides:
            claims = overrides.pop("claims")
        else:
            start = body.index(sentence)
            claims = (
                {
                    "claim_id": "dc_1",
                    "sentence_or_span": sentence,
                    "char_start": start,
                    "char_end": start + len(sentence),
                    "support_ids": tuple(support),
                    "support_kind": "BELIEF_VERSION",
                },
            )
        kwargs: dict[str, Any] = {
            "draft_id": hero.draft_id,
            "case_id": hero.case_id,
            "basis_case_revision": hero.basis_case_revision,
            "basis_proof_hash": "0" * 64,
            "action_type": "OUTBOUND_EMAIL_DISPUTE",
            "recipient": hero.recipient,
            "subject": "Disputed invoice 88431 - service terminated 31 May 2026",
            "body": body,
            "claims": claims,
            "requested_outcome": "CANCEL_INVOICE_AND_CONFIRM_CLOSURE",
            "tone": "FIRM",
            "generated_by": _attribution(),
            "generated_at": hero.now,
        }
        kwargs.update(overrides)
        return DraftAction(**kwargs)

    return _make


@pytest.fixture
def draft_payload(make_draft: DraftFactory) -> dict[str, Any]:
    """The JSON object that lands in ``action_intents.draft_payload``."""
    return drafts.draft_payload_of(make_draft())


@pytest.fixture
def open_policy(hero: Hero) -> ActionPolicy:
    """A policy that allows exactly the hero recipient and nothing else."""
    return ActionPolicy(
        allowlist=frozenset({hero.recipient}),
        execution_mode="ENABLED",
        recipient_mode="DEMO_SINK",
        demo_sink_domain="demo-sink.provenance.app",
    )


@pytest.fixture
def clock(hero: Hero) -> Callable[[], datetime]:
    return lambda: hero.now


@pytest.fixture
def memory_store() -> store.InMemoryActionStore:
    return store.InMemoryActionStore()
