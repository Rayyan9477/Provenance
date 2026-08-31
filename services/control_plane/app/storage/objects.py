"""One Protocol, one key layout, two implementations and one refusal.

See ``services/control_plane/app/storage/__init__.py`` for why a filesystem
store is the correct implementation on ``PV_PLATFORM=local`` rather than a
workaround.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal, Protocol, runtime_checkable

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

#: ``ck_source_artifacts_s3_key_shape`` is ``CHECK (s3_key LIKE 'raw/%')``.
#: Named here so a reader of :func:`raw_key` can see which constraint the shape
#: is obeying; the *test* reads the pattern out of the migration rather than
#: trusting this constant, because two copies of a constraint drift.
RAW_PREFIX: Final[str] = "raw/"

#: Section 8.18's object name under the raw prefix. Not the user's filename:
#: the filename is metadata and never becomes part of the key.
_RAW_OBJECT_NAME: Final[str] = "original"

#: How a deployment can offer bytes to a client.
UploadTransport = Literal["PRESIGNED_PUT", "LOCAL_FILESYSTEM"]

#: ``source_artifacts.s3_bucket`` is ``NOT NULL`` and names the store an object
#: lives in. A filesystem store has no bucket, and this is what it records
#: instead: a stable label that says which store, so a key resolved against the
#: wrong one is visible in the row rather than only in a 404.
LOCAL_BUCKET_NAME: Final[str] = "local-filesystem"

#: Where a laptop deployment keeps its objects when nothing else is configured.
#: Under the repository's own scratch tree rather than the system temp
#: directory, so a reviewer can find the bytes and a reboot does not silently
#: empty the store.
DEFAULT_LOCAL_ROOT: Final[str] = ".pv-objects"


class ObjectStoreError(Exception):
    """Base class, so a caller can catch the store without catching the world."""


class ObjectNotFoundError(ObjectStoreError):
    """No object at this key.

    Raised by :meth:`ObjectStore.get` rather than returning ``b""``: zero bytes
    is a legitimate object body, so an empty return would make a missing object
    indistinguishable from an empty one (``D-00-005``). :meth:`ObjectStore.head`
    returns ``None`` for the same condition, and that ``None`` is a *real
    answer* -- "there is no object at this key" -- not "not loaded".
    """


class ObjectStoreUnavailableError(ObjectStoreError):
    """This deployment has no store that can address the key.

    Raised at the call and not at construction. A deployment with no bucket
    must still start and still serve every read; refusing at startup would take
    the whole product down for a capability most requests never touch. The
    message names the environment variable a deployer would set, because
    "unavailable" on its own sends the operator to grep.
    """


@dataclass(frozen=True, slots=True)
class StoredObject:
    """What a completed write produced. Every field was measured, not declared."""

    key: str
    size_bytes: int
    sha256_hex: str
    content_type: str


@dataclass(frozen=True, slots=True)
class ObjectHead:
    """Section 8.19 step 1's ``HeadObject``: the object without its body."""

    key: str
    size_bytes: int
    sha256_hex: str
    content_type: str | None


@dataclass(frozen=True, slots=True)
class UploadTarget:
    """Where a client sends the bytes, and by what means.

    ``transport`` is not decoration. Section 8.18's cloud answer is a pre-signed
    HTTPS ``PUT``; the filesystem answer is a path. A client that cannot tell
    them apart would ``fetch()`` a ``file:`` URL and report success on a request
    the browser never made.
    """

    key: str
    url: str
    http_method: str
    transport: UploadTransport
    required_headers: dict[str, str]
    expires_at: datetime
    max_size_bytes: int


@runtime_checkable
class ObjectStore(Protocol):
    """The whole surface the control plane needs. Four verbs and two properties."""

    @property
    def bucket(self) -> str:
        """What ``source_artifacts.s3_bucket`` records for objects in this store."""

    @property
    def browser_uploadable(self) -> bool:
        """Whether a browser can send bytes to :meth:`upload_target`'s URL.

        ``main._feature_flags`` derives ``upload_ingest_enabled`` from this, so
        the flag describes what the store can do rather than whether a bucket
        name is set.
        """

    async def put(self, key: str, data: bytes, *, content_type: str) -> StoredObject: ...

    async def get(self, key: str) -> bytes: ...

    async def head(self, key: str) -> ObjectHead | None: ...

    async def upload_target(
        self, key: str, *, content_type: str, ttl_seconds: int
    ) -> UploadTarget: ...


# ---------------------------------------------------------------------------
# The key layout -- section 8.18, shared by every backend
# ---------------------------------------------------------------------------


def raw_key(*, tenant_id: uuid.UUID, user_id: uuid.UUID, artifact_id: uuid.UUID) -> str:
    """``raw/{tenant_id}/{user_id}/{artifact_id}/original``.

    The server's only key-minting function. A caller cannot influence it: the
    three ids come from the resolved principal or capability, and the object
    name is a constant. That is what makes it impossible for a client to
    redirect an upload into another tenant's prefix even holding a valid URL.
    """
    return f"{RAW_PREFIX}{tenant_id}/{user_id}/{artifact_id}/{_RAW_OBJECT_NAME}"


def normalized_key(
    *, tenant_id: uuid.UUID, user_id: uuid.UUID, artifact_id: uuid.UUID, parser_version: int
) -> str:
    """``normalized/{tenant_id}/{user_id}/{artifact_id}/parser-v{n}.json``.

    Section 8.18's second layout. Deliberately outside the ``raw/`` prefix: the
    CHECK on ``source_artifacts.s3_key`` exists so that column can only ever
    name original bytes, and a parser output stored under ``raw/`` would be
    addressable as if it were the artifact.
    """
    if parser_version < 1:
        raise ValueError(f"parser_version must be >= 1, got {parser_version}")
    return f"normalized/{tenant_id}/{user_id}/{artifact_id}/parser-v{parser_version}.json"


def _validate_key(key: str) -> PurePosixPath:
    """A key is a key, not a path.

    ``..`` is how one tenant reads another's bytes, and on a filesystem store
    it is a directory traversal rather than a metaphor. Rejected here, once,
    for every backend -- an S3 key with ``..`` in it is equally a bug.
    """
    if not key:
        raise ValueError("an object key may not be empty")
    if key.startswith("/") or key.endswith("/"):
        raise ValueError(f"an object key may not start or end with '/': {key!r}")
    if "\\" in key:
        raise ValueError(f"an object key uses '/' only: {key!r}")
    parts = key.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"an object key may not contain an empty or relative segment: {key!r}")
    return PurePosixPath(key)


# ---------------------------------------------------------------------------
# The filesystem store
# ---------------------------------------------------------------------------


class FilesystemObjectStore:
    """:class:`ObjectStore` over a directory, with the same key layout.

    The layout is mirrored on disk rather than flattened into one file per
    hash, so a human can find an artifact's bytes with ``ls`` and the cloud
    store is a genuine drop-in: the same key names the same object on both
    sides. Nothing here is a stub -- the bytes are written, digested and read
    back.

    File I/O runs on a worker thread. It is small and local, but the Protocol
    is ``async`` because its cloud implementations are network calls, and doing
    blocking I/O on the event loop inside an ``async def`` is how a local
    implementation quietly stops being a drop-in for a remote one.
    """

    __slots__ = ("_root",)

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def bucket(self) -> str:
        return LOCAL_BUCKET_NAME

    @property
    def browser_uploadable(self) -> bool:
        """``False``. A browser cannot ``PUT`` to ``file:``, and saying it can
        is the "URL that does not work" failure ``unbound.py`` named."""
        return False

    def _path(self, key: str) -> Path:
        relative = _validate_key(key)
        resolved = (self._root / Path(*relative.parts)).resolve()
        # Belt and braces: `_validate_key` already refuses `..`, and this
        # catches a symlink pointing out of the root, which it cannot.
        if not resolved.is_relative_to(self._root):
            raise ValueError(f"object key {key!r} resolves outside the store root")
        return resolved

    async def put(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        path = self._path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(_write)
        return StoredObject(
            key=key,
            size_bytes=len(data),
            sha256_hex=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
        )

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(key) from exc

    async def head(self, key: str) -> ObjectHead | None:
        path = self._path(key)
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            return None
        # The digest is recomputed rather than remembered. S3 returns a stored
        # `ChecksumSHA256`; a filesystem has no such field, and section 8.19
        # step 3 already admits "a streamed recomputation for objects under
        # 8 MiB when the checksum is absent" as the same check.
        return ObjectHead(
            key=key,
            size_bytes=len(data),
            sha256_hex=hashlib.sha256(data).hexdigest(),
            content_type=None,
        )

    async def upload_target(self, key: str, *, content_type: str, ttl_seconds: int) -> UploadTarget:
        path = self._path(key)
        await asyncio.to_thread(lambda: path.parent.mkdir(parents=True, exist_ok=True))
        return UploadTarget(
            key=key,
            url=path.as_uri(),
            http_method="PUT",
            transport="LOCAL_FILESYSTEM",
            required_headers={"Content-Type": content_type},
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
            max_size_bytes=20 * 1024 * 1024,
        )


# ---------------------------------------------------------------------------
# The refusal
# ---------------------------------------------------------------------------


class UnconfiguredObjectStore:
    """The store for a platform whose bucket and client are not here.

    ``aws`` and ``gcp`` deployments both reach this today: no
    ``S3_ARTIFACT_BUCKET``, no ``GCS_ARTIFACT_BUCKET``, and no storage client
    in the dependency set. Every method raises :class:`ObjectStoreUnavailableError`
    naming the variable, which is ``CANNOT RUN`` and not ``FAIL`` -- nothing
    was attempted and nothing went wrong.

    It is emphatically **not** a store that silently succeeds. A no-op ``put``
    followed by a working ``head`` is how a row gets written for bytes nobody
    holds, and the first symptom is a download that 404s months later against a
    row that looks perfect.
    """

    __slots__ = ("_variable",)

    def __init__(self, missing_variable: str) -> None:
        self._variable = missing_variable

    @property
    def bucket(self) -> str:
        """Raises. There is no bucket, and returning a placeholder is how a
        row gets written naming a store that does not exist."""
        raise self._refuse()

    @property
    def browser_uploadable(self) -> bool:
        return False

    def _refuse(self) -> ObjectStoreUnavailableError:
        return ObjectStoreUnavailableError(
            f"no object store is configured: {self._variable} is unset and no storage "
            "client is wired into the control plane. Set it, or run with "
            "PV_PLATFORM=local, which stores objects on the filesystem under the same "
            "key layout."
        )

    async def put(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        del key, data, content_type
        raise self._refuse()

    async def get(self, key: str) -> bytes:
        del key
        raise self._refuse()

    async def head(self, key: str) -> ObjectHead | None:
        del key
        raise self._refuse()

    async def upload_target(self, key: str, *, content_type: str, ttl_seconds: int) -> UploadTarget:
        del key, content_type, ttl_seconds
        raise self._refuse()


# ---------------------------------------------------------------------------
# The factory
# ---------------------------------------------------------------------------

#: Which environment variable a platform's operator would set. Used only to
#: build the refusal message, so an operator on the wrong platform is told the
#: name rather than the concept.
_BUCKET_VARIABLE: Final[dict[str, str]] = {
    "aws": "S3_ARTIFACT_BUCKET",
    "gcp": "GCS_ARTIFACT_BUCKET",
}


def object_store_for(settings: Any) -> ObjectStore:
    """``PV_PLATFORM`` decides, and nothing else does.

    Same rule as ``ApiConfig._identity_from_settings``: one switch selects the
    provider, so storage cannot disagree with authentication about which cloud
    this is. ``settings`` is typed ``Any`` for the same reason that function
    takes it -- importing ``Settings`` here would read the environment at
    import time and make the hermetic suites depend on a dotenv.
    """
    platform = getattr(settings, "pv_platform", "aws") or "aws"
    if platform == "local":
        # Read from the process environment rather than from ``Settings``:
        # adding a field there is a change to a file this task does not own,
        # and ``ApiConfig`` reads ``PV_LOCAL_AUTH_SECRET`` and ``GIT_SHA`` the
        # same way for the same reason. It defaults rather than raising because
        # an object root is a location, not a credential.
        root = (
            getattr(settings, "pv_local_object_root", None)
            or os.environ.get("PV_LOCAL_OBJECT_ROOT")
            or DEFAULT_LOCAL_ROOT
        )
        return FilesystemObjectStore(root)
    return UnconfiguredObjectStore(_BUCKET_VARIABLE.get(platform, "S3_ARTIFACT_BUCKET"))
