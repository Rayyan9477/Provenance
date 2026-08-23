"""The draft digest -- one canonical serialization, hashed the same way twice.

``T9.2``, first sub-task: "Compute the draft hash over a canonical
serialization, so whitespace changes are either meaningful in both places or in
neither."

Why this file exists separately from the approval tests
--------------------------------------------------------
``approval_draft_sha256`` is the load-bearing half of the canon sentence *bind
approval to case revision **and** draft SHA-256*. If the hash is unstable, the
approval binding is decorative: every execution would refuse, and the failure
would read as a concurrency bug rather than a serialization one. So the digest
gets its own assertions, before anything is bound to it.

``specs/15_API_SPEC.md`` section 8.26 step 7 fixes the definition:
``approval_draft_sha256 = sha256(JCS(approved_draft))``. Section 9.11 re-checks
``sha256(JCS(draft_payload))`` against it at execution time. Those two must be
the same function, so there is exactly one here.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from services.control_plane.app.actions import drafts

pytestmark = pytest.mark.unit


def test_key_order_does_not_change_the_digest() -> None:
    """JCS sorts keys. Two orderings of one object are one message."""
    a = {"subject": "Disputed invoice 88431", "body": "Hello,\n\nAlex"}
    b = {"body": "Hello,\n\nAlex", "subject": "Disputed invoice 88431"}

    assert drafts.draft_digest(a) == drafts.draft_digest(b)


def test_insignificant_whitespace_between_keys_does_not_change_the_digest() -> None:
    """The digest is over the parsed object, not over the bytes a client sent."""
    payload = {"subject": "s", "body": "b"}
    spaced = json.loads(json.dumps(payload, indent=4))

    assert drafts.draft_digest(payload) == drafts.draft_digest(spaced)


def test_a_changed_body_changes_the_digest() -> None:
    """The whole point. One character of the letter is a different letter."""
    before = {"subject": "s", "body": "I expect a refund."}
    after = {"subject": "s", "body": "I expect a refund!"}

    assert drafts.draft_digest(before) != drafts.draft_digest(after)


def test_whitespace_inside_the_body_is_significant() -> None:
    """Whitespace *in the message* is content; whitespace *in the JSON* is not.

    A trailing space the user typed is part of what they approved. Collapsing
    it would mean the hash covers something other than the thing on the screen.
    """
    assert drafts.draft_digest({"body": "Alex"}) != drafts.draft_digest({"body": "Alex "})


def test_the_digest_is_thirty_two_raw_bytes() -> None:
    """``ck_action_intents_draft_sha`` is ``length(draft_sha256) = 32``.

    The column is ``BYTES``. Storing the 64-character hex string would satisfy
    no CHECK and would be caught only at insert time, in whichever phase first
    tried it.
    """
    digest = drafts.draft_digest({"subject": "s", "body": "b"})

    assert isinstance(digest, bytes)
    assert len(digest) == 32
    assert drafts.draft_digest_hex({"subject": "s", "body": "b"}) == digest.hex()


def test_the_digest_agrees_with_a_hand_computed_sha256() -> None:
    """Stated in full rather than by round-trip.

    A test that only compares the function to itself passes even when the
    function is wrong in the same way twice.
    """
    payload = {"body": "b", "subject": "s"}
    expected = hashlib.sha256(b'{"body":"b","subject":"s"}').digest()

    assert drafts.draft_digest(payload) == expected


def test_non_ascii_is_hashed_as_utf8_without_escaping() -> None:
    """``ensure_ascii=False``, matching ``app/api/idempotency.py``.

    A counterparty name with an accent in it must hash identically on both
    sides of the approval, and the two sides are written by different modules.
    """
    accented = {"subject": "Facture réglée"}

    assert drafts.canonical_json_bytes(accented) == '{"subject":"Facture réglée"}'.encode()
    assert drafts.draft_digest(accented) != drafts.draft_digest({"subject": "Facture reglee"})


def test_the_stored_payload_round_trips_through_json(make_draft) -> None:
    """``draft_payload`` is a JSONB column; every value in it must be JSON.

    A ``uuid.UUID`` or a ``datetime`` that survives to the insert becomes
    either a psycopg adaptation error or, worse, a string whose format nobody
    pinned. Serialising through the contract's own JSON mode settles it once.
    """
    payload = drafts.draft_payload_of(make_draft())

    assert json.loads(json.dumps(payload)) == payload
    assert payload["subject"].startswith("Disputed invoice 88431")
    assert payload["claims"][0]["support_ids"] == [
        "aaaaaaaa-0000-4000-8000-000000000002",
    ]


def test_the_minted_idempotency_key_is_stable_for_one_draft(hero) -> None:
    """``0007``'s docstring: ``sha256(tenant || user || case || type || draft)``.

    Stable, so two proposals of the same draft collide on
    ``uq_action_intents_idempotency`` rather than creating two intents; global,
    so a key stolen from another tenant cannot collide into a send.
    """
    digest = drafts.draft_digest({"subject": "s", "body": "b"})
    args = {
        "tenant_id": hero.tenant_id,
        "user_id": hero.user_id,
        "case_id": hero.case_id,
        "action_type": "OUTBOUND_EMAIL_DISPUTE",
        "draft_sha256": digest,
    }

    first = drafts.mint_idempotency_key(**args)

    assert first == drafts.mint_idempotency_key(**args)
    assert len(first) == 64
    assert first != drafts.mint_idempotency_key(**{**args, "user_id": uuid.uuid4()})
