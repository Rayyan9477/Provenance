"""The draft digest: one canonical serialization, hashed the same way twice.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Memory, action, and time*, row **External
  action**: bind approval to case revision **and** draft SHA-256.
- ``docs/specs/15_API_SPEC.md`` section 8.26 step 7 --
  ``approval_draft_sha256 = sha256(JCS(approved_draft))`` -- and section 9.11,
  which re-checks ``sha256(JCS(draft_payload))`` against it immediately before
  the send.
- ``db/migrations/versions/0007_action_plane.py`` --
  ``ck_action_intents_draft_sha`` is ``length(draft_sha256) = 32``, so the
  column holds raw bytes and never hex.

Why the two hashes must be one function
----------------------------------------
The approval writes a digest and the executor recomputes one. If those two
computations live in two places they will eventually disagree about key order,
about ``ensure_ascii``, or about whether ``None`` serialises -- and the symptom
is a ``409 ACTION_STALE`` on every single execution, which reads like a
concurrency bug and is a serialization bug. ``15_API_SPEC.md`` section 22 flags
exactly this class of failure as "the single easiest place to introduce a
self-invalidating approval". There is therefore one digest function here and
both sides call it.

Why not ``DraftAction.sha256()``
---------------------------------
``provenance_contracts.actions.DraftAction.sha256`` hashes the *contract
object* with ``draft_id``, ``generated_at`` and ``generated_by`` excluded. That
is the right digest for "did the Advocate produce the same draft twice". It is
the wrong digest for the approval binding, because the thing a human approved
is a JSON object that arrived over HTTP -- ``{"subject": ..., "body": ...}`` --
and the executor has only the stored ``draft_payload`` JSONB to re-hash. This
module hashes the payload; the contract hashes the model. Both are correct and
they answer different questions.

Canonicalisation
----------------
RFC 8785 to the depth this payload needs, and byte-identical to
``app/api/idempotency.py::jcs_canonicalize`` -- sorted keys, no insignificant
whitespace, UTF-8 without escaping, ``NaN``/``Infinity`` refused. It is
reimplemented rather than imported because ``app/actions`` must not depend on
``app/api``: the action plane is called *by* the API, and an import in the
other direction would make the executor unusable from a worker.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from typing import Any, Final

from provenance_contracts.actions import DraftAction

__all__ = [
    "DRAFT_PAYLOAD_EXCLUDE",
    "EDITABLE_DRAFT_FIELDS",
    "canonical_json_bytes",
    "draft_digest",
    "draft_digest_hex",
    "draft_payload_of",
    "merge_approved_draft",
    "mint_idempotency_key",
]

#: Dropped from the stored payload. ``draft_id`` and ``generated_at`` identify
#: the *generation*, not the message, so a regeneration that produces the
#: identical letter must not change the digest and invalidate an approval.
#: ``generated_by`` is kept: which model wrote the words a human is about to
#: send in their own name is part of what was approved.
DRAFT_PAYLOAD_EXCLUDE: Final[frozenset[str]] = frozenset({"draft_id", "generated_at"})

#: The only two fields ``PUT /v1/action-intents/{id}/draft`` may replace.
#: ``recipient`` is deliberately absent -- section 8.25: changing it would
#: change the action's blast radius after grounding validation ran against a
#: specific counterparty. A different recipient requires a new intent.
EDITABLE_DRAFT_FIELDS: Final[tuple[str, ...]] = ("subject", "body")


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """*payload* as canonical JSON bytes.

    Sorted keys, no whitespace, UTF-8 unescaped, ``NaN`` refused. Whitespace
    *inside* a string value is preserved: a trailing space the user typed is
    part of the message they approved, and collapsing it would mean the digest
    covers something other than what was on the screen.
    """
    return json.dumps(
        payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def draft_digest(payload: Mapping[str, Any]) -> bytes:
    """SHA-256 of :func:`canonical_json_bytes`, as the 32 raw bytes the column holds."""
    return hashlib.sha256(canonical_json_bytes(payload)).digest()


def draft_digest_hex(payload: Mapping[str, Any]) -> str:
    """:func:`draft_digest` as lower-case hex, for API responses and log lines."""
    return draft_digest(payload).hex()


def draft_payload_of(draft: DraftAction) -> dict[str, Any]:
    """The JSON object that lands in ``action_intents.draft_payload``.

    Serialised through the contract's own JSON mode, so every ``uuid.UUID`` and
    every ``datetime`` is already a string by the time psycopg sees it. A
    ``UUID`` that survives to the insert becomes either an adaptation error or,
    worse, a string in a format nobody pinned -- and the digest would then
    depend on which of those happened.
    """
    payload = draft.model_dump(mode="json")
    for field in DRAFT_PAYLOAD_EXCLUDE:
        payload.pop(field, None)
    return payload


def merge_approved_draft(stored: Mapping[str, Any], approved: Mapping[str, Any]) -> dict[str, Any]:
    """The stored payload with the approved subject and body written over it.

    Section 8.26: "The server hashes this, not the stored draft." The client
    submits what was on the screen; the digest must cover that. But the stored
    payload also carries the claims, the support ids and the requested outcome,
    none of which is editable and all of which the human saw -- so the digest
    covers the merge rather than the fragment.

    The result is a pure function of the two editable fields plus row content
    the user could not have changed. Nothing the user did not see can enter it,
    and nothing the user saw can leave it, which is the property section 8.26
    is actually protecting: a race between a draft edit and an approval cannot
    cause a different message to be sent than the one on the screen.
    """
    merged = dict(stored)
    for field in EDITABLE_DRAFT_FIELDS:
        if field in approved:
            merged[field] = approved[field]
    return merged


def mint_idempotency_key(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    case_id: uuid.UUID,
    action_type: str,
    draft_sha256: bytes,
) -> str:
    """``sha256(tenant || user || case || action_type || draft_sha256)``, hex.

    ``0007``'s docstring, verbatim, and the reason it is global rather than
    per-user: ``uq_action_intents_idempotency`` is ``UNIQUE
    (idempotency_key)``, so global uniqueness is free and a key stolen from
    another tenant cannot collide into a send.

    CREATION ONLY. Never use this to derive an execution key. Section 9.11 says
    the execute key MUST equal ``action_intents.idempotency_key``, and this
    function does not reliably reproduce it, for two independent reasons:

    1. Section 9.8 step 7 stores the **request** key when the Advocate supplied
       one -- the Advocate's retry must collide with its own first attempt
       rather than with a recomputation -- so the row's key was very likely
       never a mint at all.
    2. The material covers ``draft_sha256``, and approving with any edit moves
       that digest. So even a row created from a mint stops matching its own
       recomputation the moment a human changes a word.

    Either one alone turns every execution of a legitimately approved intent
    into a ``409 IDEMPOTENCY_CONFLICT``. Read ``intent.idempotency_key`` off
    the row the executor already loads.
    ``tests/actions/test_executor.py::test_the_mint_is_not_a_valid_source_for_the_execution_key``
    pins the divergence.
    """
    material = b"".join(
        [
            tenant_id.bytes,
            user_id.bytes,
            case_id.bytes,
            action_type.encode("utf-8"),
            draft_sha256,
        ]
    )
    return hashlib.sha256(material).hexdigest()
