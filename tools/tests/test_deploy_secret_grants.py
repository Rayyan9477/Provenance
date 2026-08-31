"""Every secret the deploy mounts must also be granted to the runtime account.

`deploy/cloudrun.sh` keeps two lists that have to agree and are three hundred
lines apart: the secrets it grants `roles/secretmanager.secretAccessor` on, and
the secrets it mounts into a revision with `--set-secrets`.

They had drifted twice.

`provenance-db-ca-cert` -- the CockroachDB cluster CA, which the script spends
twenty lines explaining the deploy cannot proceed without -- was mounted at
`${CA_MOUNT}` and never granted. `provenance-api-token` drifted later, when the
web application's bearer token was moved out of `--set-env-vars` (where it was
a live credential in a world-readable revision spec) into a secret reference;
the mount moved and the grant did not.

Neither failed on the machine this was built on, because its runtime service
account already held the access from an earlier manual grant. That is the worst
shape a deployment bug can take: correct on the author's machine, broken for
everyone else, and silent in both cases. A judge running `deploy/cloudrun.sh up`
in a fresh project would have got a control plane that could not read its own
database certificate.

This test compares the two lists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "cloudrun.sh"

#: `--set-secrets NAME=secret:latest` and `--set-secrets /path=secret:latest`.
_MOUNTED = re.compile(r"=(provenance-[a-z0-9-]+):latest")

#: The `for s in ... ; do` list that feeds `gcloud secrets add-iam-policy-binding`.
_GRANT_LOOP = re.compile(r"for s in (provenance-[\s\S]*?); do")

#: A grant made outside the loop, beside the secret it has just minted.
#: `provenance-api-token` is signed against the deployed control plane, so it
#: does not exist when the loop runs and is granted at its mint site instead.
#: The test asks whether a secret is granted ANYWHERE, not whether it is in the
#: loop -- a rule that demanded the loop would force a spurious binding attempt
#: on a secret that is not there yet.
_DIRECT_GRANT = re.compile(r"gcloud secrets add-iam-policy-binding\s+(provenance-[a-z0-9-]+)")


def _script() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def _mounted() -> set[str]:
    return set(_MOUNTED.findall(_script()))


def _granted() -> set[str]:
    """Every secret this script binds the accessor role on, however it does it."""
    script = _script()
    loop = _GRANT_LOOP.search(script)
    assert loop is not None, "the grant loop is gone; this test no longer measures anything"
    return set(re.findall(r"provenance-[a-z0-9-]+", loop.group(1))) | set(
        _DIRECT_GRANT.findall(script)
    )


def test_the_script_still_mounts_secrets() -> None:
    """Guard the guard: a regex that matches nothing would pass everything."""
    mounted = _mounted()
    assert len(mounted) >= 8, f"only {len(mounted)} mounted secrets found; the pattern has rotted"


def test_every_mounted_secret_is_granted() -> None:
    missing = sorted(_mounted() - _granted())
    assert not missing, (
        "these secrets are mounted into a revision but never granted to the "
        "runtime service account, so a deploy into a project without a "
        f"pre-existing manual binding gets a revision that cannot read them: {missing}"
    )


def test_the_cluster_ca_is_granted() -> None:
    """Named on its own because it is the one the script says is mandatory."""
    assert "provenance-db-ca-cert" in _granted(), (
        "the CockroachDB cluster CA is not in the grant loop; without it the "
        "service starts, reports db_ok:false, and logs 'certificate verify "
        "failed', which reads like a bad password"
    )


def test_nothing_is_granted_that_is_never_mounted() -> None:
    """The other direction: a grant for a secret nothing uses is stale."""
    stale = sorted(_granted() - _mounted())
    assert not stale, (
        f"these secrets are granted but mounted nowhere; either the mount was "
        f"removed and the grant left behind, or the name is wrong: {stale}"
    )
