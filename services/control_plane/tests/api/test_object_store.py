"""The object store: one Protocol, one key layout, and a platform that decides.

Authority
---------
- ``specs/15_API_SPEC.md`` section 8.18 -- the key layout is fixed at
  ``raw/{tenant_id}/{user_id}/{artifact_id}/original`` and the *server* chooses
  it.
- ``db/migrations/versions/0002_evidence_plane.py`` --
  ``ck_source_artifacts_s3_key_shape`` is ``CHECK (s3_key LIKE 'raw/%')``, so
  the layout is enforced by the database and not only by convention.
- ``CANONICAL_DECISIONS.md`` -> *Operating-mode disclosure*: what a deployment
  can actually do is read off what is configured, never off a switch.

Why a filesystem store is the implementation and not a workaround
------------------------------------------------------------------
``PV_PLATFORM=local`` is, in ``Settings``' own words, "the mode a reviewer runs
on a laptop with no cloud account at all". ``S3_ARTIFACT_BUCKET``,
``GCS_ARTIFACT_BUCKET`` and ``GOOGLE_CLOUD_PROJECT`` are all unset on this
build. A filesystem store that mirrors the same key layout is therefore the
*correct* store for this platform, and the cloud store is a drop-in for it --
which is why the key helpers below are shared rather than reimplemented per
backend.

The vacuity guard that matters
-------------------------------
``test_the_raw_key_satisfies_the_applied_check`` reads the LIKE pattern out of
the migration instead of restating it. A constant here would be a second copy
of a database constraint, and the failure mode of a second copy is that it goes
on agreeing with itself after the first one moves.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import pytest

from services.control_plane.app.storage import (
    FilesystemObjectStore,
    ObjectNotFoundError,
    ObjectStoreUnavailableError,
    UnconfiguredObjectStore,
    normalized_key,
    object_store_for,
    raw_key,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[4]
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations" / "versions"

TENANT = uuid.UUID("018f7a00-0000-7000-8000-000000000001")
USER = uuid.UUID("018f7a01-0000-7000-8000-000000000001")
ARTIFACT = uuid.UUID("018f9e80-0000-7000-8000-000000000001")


class _Settings:
    """The three attributes the factory reads. Nothing else is consulted."""

    def __init__(self, **kwargs: Any) -> None:
        self.pv_platform = kwargs.pop("pv_platform", "local")
        self.s3_artifact_bucket = kwargs.pop("s3_artifact_bucket", None)
        self.gcs_artifact_bucket = kwargs.pop("gcs_artifact_bucket", None)
        self.pv_local_object_root = kwargs.pop("pv_local_object_root", None)
        assert not kwargs, kwargs


def _s3_key_like_pattern() -> str:
    """The prefix ``ck_source_artifacts_s3_key_shape`` requires, from the DDL."""
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS_DIR.glob("[0-9]*.py"))
    )
    match = re.search(
        r"ck_source_artifacts_s3_key_shape\s+CHECK\s*\(\s*s3_key\s+LIKE\s+'([^']+)'\s*\)",
        source,
    )
    assert match is not None, "no migration declares ck_source_artifacts_s3_key_shape"
    pattern = match.group(1)
    assert pattern.endswith("%") and len(pattern) > 1, pattern
    return pattern[:-1]


def test_the_raw_key_satisfies_the_applied_check_and_section_8_18_layout() -> None:
    """The one key layout, checked against the constraint that enforces it."""
    required = _s3_key_like_pattern()
    assert required, "the CHECK requires no prefix at all; this test measures nothing"

    key = raw_key(tenant_id=TENANT, user_id=USER, artifact_id=ARTIFACT)
    assert key.startswith(required), (key, required)
    assert key == f"raw/{TENANT}/{USER}/{ARTIFACT}/original"
    # Every id appears, and in the order section 8.18 prints. A layout that
    # merely started with `raw/` would satisfy the CHECK and lose the scoping.
    assert key.split("/")[1:4] == [str(TENANT), str(USER), str(ARTIFACT)]


def test_the_normalized_key_is_the_parser_output_location_section_8_18_names() -> None:
    key = normalized_key(tenant_id=TENANT, user_id=USER, artifact_id=ARTIFACT, parser_version=2)
    assert key == f"normalized/{TENANT}/{USER}/{ARTIFACT}/parser-v2.json"
    assert not key.startswith("raw/"), "parser output must not land in the raw prefix"


async def test_the_filesystem_store_round_trips_bytes_under_the_key_layout(
    tmp_path: Path,
) -> None:
    """Put, head, get. The bytes that come back are the bytes that went in."""
    store = FilesystemObjectStore(tmp_path)
    key = raw_key(tenant_id=TENANT, user_id=USER, artifact_id=ARTIFACT)
    payload = b"Subject: Invoice for June service\r\n\r\nAmount due USD 186.00\n"

    stored = await store.put(key, payload, content_type="message/rfc822")
    assert stored.key == key
    assert stored.size_bytes == len(payload)

    head = await store.head(key)
    assert head is not None
    assert head.size_bytes == len(payload)
    assert head.sha256_hex == stored.sha256_hex

    assert await store.get(key) == payload
    # The layout is mirrored on disk, so the cloud store is a drop-in and a
    # human can find the bytes without a client.
    assert (tmp_path / "raw" / str(TENANT) / str(USER) / str(ARTIFACT) / "original").is_file()


async def test_head_of_an_unwritten_key_is_absence_and_get_refuses(tmp_path: Path) -> None:
    """``None`` here means "no object at this key", which is a real answer.

    ``get`` raises rather than returning ``b""``: zero bytes is a legitimate
    object body, so a caller could not tell an empty object from a missing one
    (``D-00-005``).
    """
    store = FilesystemObjectStore(tmp_path)
    key = raw_key(tenant_id=TENANT, user_id=USER, artifact_id=ARTIFACT)

    assert await store.head(key) is None
    with pytest.raises(ObjectNotFoundError):
        await store.get(key)

    await store.put(key, b"", content_type="text/plain")
    head = await store.head(key)
    assert head is not None and head.size_bytes == 0, "an empty object must read as an object"
    assert await store.get(key) == b""


async def test_the_store_refuses_a_key_that_would_escape_its_root(tmp_path: Path) -> None:
    """A key is a key, not a path. ``..`` is how one tenant reads another."""
    store = FilesystemObjectStore(tmp_path)
    for hostile in ("../escape", "raw/../../escape", "/absolute", "raw//double", ""):
        with pytest.raises(ValueError):
            await store.put(hostile, b"x", content_type="text/plain")


async def test_the_store_choice_follows_pv_platform(tmp_path: Path) -> None:
    """``PV_PLATFORM`` decides, and nothing else does."""
    local = object_store_for(_Settings(pv_platform="local", pv_local_object_root=str(tmp_path)))
    assert isinstance(local, FilesystemObjectStore)

    for platform in ("aws", "gcp"):
        remote = object_store_for(_Settings(pv_platform=platform))
        assert isinstance(remote, UnconfiguredObjectStore), platform


async def test_an_unconfigured_store_refuses_and_names_the_missing_client() -> None:
    """A store that cannot address an object says so, and says what it needs.

    It does not raise at construction: a deployment with no bucket must still
    start and still serve every read. The refusal belongs at the call, and it
    has to name the variable a deployer would set -- otherwise the operator
    reads "unavailable" and has nowhere to go.
    """
    store = object_store_for(_Settings(pv_platform="aws"))
    key = raw_key(tenant_id=TENANT, user_id=USER, artifact_id=ARTIFACT)

    with pytest.raises(ObjectStoreUnavailableError) as put_exc:
        await store.put(key, b"x", content_type="text/plain")
    assert "S3_ARTIFACT_BUCKET" in str(put_exc.value)

    with pytest.raises(ObjectStoreUnavailableError):
        await store.get(key)
    with pytest.raises(ObjectStoreUnavailableError):
        await store.head(key)

    gcp = object_store_for(_Settings(pv_platform="gcp"))
    with pytest.raises(ObjectStoreUnavailableError) as gcp_exc:
        await gcp.head(key)
    assert "GCS_ARTIFACT_BUCKET" in str(gcp_exc.value)


async def test_the_upload_target_declares_the_transport_it_actually_offers(
    tmp_path: Path,
) -> None:
    """A pre-signed URL that nothing can PUT to is worse than a refusal.

    The filesystem store mints no HTTPS URL and does not pretend to. It reports
    ``LOCAL_FILESYSTEM`` and a ``file:`` locator, and
    :attr:`browser_uploadable` is ``False`` -- which is what
    ``upload_ingest_enabled`` is derived from, so a UI is never sent to an
    upload screen that cannot work.
    """
    store = FilesystemObjectStore(tmp_path)
    key = raw_key(tenant_id=TENANT, user_id=USER, artifact_id=ARTIFACT)

    target = await store.upload_target(key, content_type="message/rfc822", ttl_seconds=900)
    assert target.transport == "LOCAL_FILESYSTEM"
    assert target.url.startswith("file:")
    assert key.replace("/", "") in target.url.replace("/", "").replace("\\", "")
    assert store.browser_uploadable is False

    unconfigured = object_store_for(_Settings(pv_platform="gcp"))
    assert unconfigured.browser_uploadable is False
    with pytest.raises(ObjectStoreUnavailableError):
        await unconfigured.upload_target(key, content_type="text/plain", ttl_seconds=900)
