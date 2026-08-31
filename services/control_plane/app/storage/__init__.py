"""The object store: where artifact bytes live, behind one swappable Protocol.

Authority
---------
- ``specs/15_API_SPEC.md`` section 8.18 -- the key layout is fixed at
  ``raw/{tenant_id}/{user_id}/{artifact_id}/original`` and parser output at
  ``normalized/{tenant_id}/{user_id}/{artifact_id}/parser-v{n}.json``. The
  *server* chooses the key; the client never does.
- ``db/migrations/versions/0002_evidence_plane.py`` --
  ``ck_source_artifacts_s3_key_shape`` is ``CHECK (s3_key LIKE 'raw/%')``. The
  layout is a database constraint, not a convention.
- ``CANONICAL_DECISIONS.md`` -> *Operating-mode disclosure*.

Why a filesystem store is the implementation on this platform
--------------------------------------------------------------
``S3_ARTIFACT_BUCKET``, ``GCS_ARTIFACT_BUCKET`` and ``GOOGLE_CLOUD_PROJECT``
are unset on this build and ``PV_PLATFORM=local``, which ``Settings`` documents
as "the mode a reviewer runs on a laptop with no cloud account at all". A
filesystem store that mirrors the same key layout is therefore the *correct*
store for this platform rather than a stand-in for one: the bytes are really
stored, really addressed by the key ``source_artifacts.s3_key`` records, and
really read back. Swapping in S3 or GCS is a change of one class, because the
key helpers are shared and every caller depends on :class:`ObjectStore`.

What the Protocol deliberately does not promise
------------------------------------------------
A **pre-signed URL**. Section 8.18 returns one on a cloud platform and there is
no filesystem equivalent: a browser cannot ``PUT`` to ``file:``. So
:meth:`ObjectStore.upload_target` returns an :class:`UploadTarget` carrying the
transport it actually offers, and :attr:`ObjectStore.browser_uploadable` says
whether a browser can use it. ``main._feature_flags`` reads that property, so
``upload_ingest_enabled`` describes what the store can do rather than whether a
bucket name happens to be set. ``adapters/unbound.py`` recorded the rule this
obeys: "returning a URL that does not work would be worse than refusing".

Network, and where these calls may not appear
-----------------------------------------------
Every method here is ``async`` because the cloud implementations are network
calls. ``CANONICAL_DECISIONS.md`` -> *Transaction isolation* forbids a network
call inside a serializable transaction callback, and object-store I/O is a
network call in every implementation but this one. Callers therefore do their
store I/O **before** they open a connection, and ``python -m
tools.txn_purity_lint`` is the check that keeps it that way.
"""

from __future__ import annotations

from services.control_plane.app.storage.objects import (
    LOCAL_BUCKET_NAME,
    RAW_PREFIX,
    FilesystemObjectStore,
    ObjectHead,
    ObjectNotFoundError,
    ObjectStore,
    ObjectStoreError,
    ObjectStoreUnavailableError,
    StoredObject,
    UnconfiguredObjectStore,
    UploadTarget,
    UploadTransport,
    normalized_key,
    object_store_for,
    raw_key,
)

__all__ = [
    "LOCAL_BUCKET_NAME",
    "RAW_PREFIX",
    "FilesystemObjectStore",
    "ObjectHead",
    "ObjectNotFoundError",
    "ObjectStore",
    "ObjectStoreError",
    "ObjectStoreUnavailableError",
    "StoredObject",
    "UnconfiguredObjectStore",
    "UploadTarget",
    "UploadTransport",
    "normalized_key",
    "object_store_for",
    "raw_key",
]
