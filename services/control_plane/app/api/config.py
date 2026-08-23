"""Runtime configuration and the dependency container.

Authority: ``specs/15_API_SPEC.md`` sections 8.2 and 14.

Why this is not ``provenance_contracts.settings.Settings``
----------------------------------------------------------
``Settings`` requires the full production environment -- database DSNs, S3
buckets, Cognito secret ARNs -- and reads it once at import. The API needs a
much smaller, already-resolved view, and the hermetic suites must build an app
with no environment at all. :func:`ApiConfig.from_settings` is the bridge:
production wiring resolves ``Settings`` once and hands over an
:class:`ApiConfig`; tests construct one directly.

One naming note, recorded rather than smoothed over: the *response field* is
``git_sha`` (section 8.2 is explicit that ``build_sha`` is not a field name),
while the *environment variable* ``Settings`` reads is ``BUILD_SHA``. Both are
correct in their own layer, and the mapping happens here, once.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from services.control_plane.app.api.idempotency import IdempotencyStore
    from services.control_plane.app.api.ports import (
        InternalPort,
        ReadPort,
        UserDirectory,
        WritePort,
    )
    from services.control_plane.app.auth.identity import (
        IdentityProvider,
        IdentityProviderName,
    )
    from services.control_plane.app.auth.jwt import JwksProvider

__all__ = ["ApiConfig", "Dependencies"]

AgentMode = Literal["LIVE", "FIXTURE", "DEGRADED"]
OtlpExport = Literal["ENABLED", "DISABLED", "FAILING"]

#: Which cloud this deployment runs on. Mirrors ``Settings.pv_platform``
#: (``PV_PLATFORM``) rather than being a second, independently settable
#: switch: authentication must not be able to disagree with storage about
#: which cloud we are on. It defaults to ``"aws"`` because that is the
#: behaviour this object had before the pivot, and because the hermetic
#: suites build Cognito-shaped configurations without naming a platform.
PlatformName = Literal["aws", "gcp", "local"]

#: Section 2.1's scope-allocation table. On Cognito it lives in the resource
#: server and arrives inside the token, so this stays empty there.
_NO_DEFAULT_SCOPES: Mapping[str, frozenset[str]] = {}

#: Section 8.18. Executables and archives are rejected outright.
ALLOWED_UPLOAD_MIME_TYPES: tuple[str, ...] = (
    "application/pdf",
    "image/png",
    "image/jpeg",
    "text/plain",
    "message/rfc822",
)

#: Used only where the corresponding AWS variable is absent because the
#: deployment is not on AWS. Neither is a credential and neither silently
#: enables anything: the ingest domain names where inbound mail would arrive,
#: and ``region`` is a label on ``GET /v1/version``.
DEFAULT_INGEST_DOMAIN: str = "in.provenance.invalid"
DEFAULT_REGION: str = "unknown"

#: Section 2.1's scope-allocation table, verbatim. On Cognito this lives in
#: the resource server and arrives inside the token. Google Identity Platform
#: has no resource server and the local issuer has no console, so on those two
#: platforms the same table is applied server-side, keyed on the logical
#: client -- still configuration, still never request data, and still only
#: consulted when the token carries no ``scope`` of its own.
#:
#: Note what is *not* here: ``provenance-agent-runtime`` holds no
#: ``action/execute`` and no ``ingest/write``. The graph that drafts an
#: outbound letter remains structurally incapable of sending it.
DEFAULT_SCOPE_GRANTS: Mapping[str, frozenset[str]] = {
    "provenance-web": frozenset({"provenance.memory/read"}),
    "provenance-agent-runtime": frozenset(
        {
            "provenance.memory/read",
            "provenance.memory/propose",
            "provenance.action/propose",
        }
    ),
    "provenance-workers": frozenset(
        {
            "provenance.memory/read",
            "provenance.ingest/write",
            "provenance.trigger/evaluate",
            "provenance.action/execute",
            "provenance.outbox/dispatch",
        }
    ),
}

#: The ``provenance_client`` custom-claim values the two workload identities
#: carry off AWS. They are identifiers rather than secrets -- a Cognito app
#: client id is not a secret either -- and they are trustworthy for the same
#: reason: only a privileged server can write the claim that carries them.
WORKLOAD_CLIENT_IDS: Mapping[str, str] = {
    "provenance-agent-runtime": "provenance-agent-runtime",
    "provenance-workers": "provenance-workers",
}

#: The local issuer's three clients. Fixed, because there is no console to
#: read them from and a judge should not have to invent three identifiers.
LOCAL_CLIENT_ID_NAMES: Mapping[str, str] = {
    "provenance-web": "provenance-web",
    "provenance-agent-runtime": "provenance-agent-runtime",
    "provenance-workers": "provenance-workers",
}


def _three_clients(web: str, agent: str, worker: str) -> dict[str, str]:
    """Three distinct ids, or a refusal.

    Building the map inline is what let three unset ids collapse into a
    one-entry dict that read as configured and behaved as unconfigured. A dict
    literal cannot report that; this can.
    """
    names = {web: "provenance-web", agent: "provenance-agent-runtime", worker: "provenance-workers"}
    if len(names) != 3:
        raise ValueError(
            "The three app client ids must be distinct and set; "
            f"{3 - len(names)} collapsed into another."
        )
    return names


def _google_client_id_names(settings: Any, project: str) -> dict[str, str]:
    """Map Google Identity Platform callers onto section 2.4's logical names.

    The browser surface is keyed on the **project id**, because that is a GIP
    token's ``aud`` and is what an ordinary signed-in user's token resolves to
    when it carries no ``provenance_client`` custom claim. The two workload
    identities are keyed on the claim value they are provisioned with. An
    identity carrying neither reaches ``UNKNOWN_CLIENT`` and therefore no
    route class, which is the section 2.4 property intact.
    """
    del settings
    return _three_clients(
        project,
        WORKLOAD_CLIENT_IDS["provenance-agent-runtime"],
        WORKLOAD_CLIENT_IDS["provenance-workers"],
    )


@dataclass(frozen=True, slots=True)
class ApiConfig:
    #: The OIDC issuer of whichever identity provider is active. The field
    #: keeps its Cognito-era name for the same reason ``users.cognito_sub``
    #: does -- a rename here is a rename in the hermetic fixtures, which this
    #: task does not own -- and :attr:`issuer` is the provider-neutral name new
    #: code should read. On ``platform="gcp"`` it holds the Google Identity
    #: Platform ``securetoken`` issuer; on ``"local"``, the local one.
    cognito_issuer: str
    client_id_names: Mapping[str, str]
    # ``repr=False`` on all three signing keys, and it is not cosmetic.
    # ``D-00-019`` is a live credential that reached a pytest failure header;
    # a frozen dataclass renders every field, and this object is constructed
    # in fixtures and therefore printed in exactly that position. It was
    # observed happening -- ``cursor_hmac_key=b'...'`` in a failure banner --
    # while this task was in progress.
    cursor_hmac_key: bytes = field(repr=False)
    capability_hmac_key: bytes = field(repr=False)
    ingest_domain: str
    service: str = "provenance-control-plane"
    version: str = "1.0.0"
    api_version: str = "v1"
    contracts_schema_version: str = "1.0"
    git_sha: str = "unknown"
    region: str = "us-east-1"
    built_at: datetime | None = None
    schema_revision: str | None = None
    fixture_mode: bool = False
    agent_mode: AgentMode = "LIVE"
    otlp_export: OtlpExport = "DISABLED"
    judge_group: str = "provenance-judges"
    judge_allowlist_enabled: bool = True
    max_artifact_bytes: int = 20_971_520
    allowed_upload_mime_types: tuple[str, ...] = ALLOWED_UPLOAD_MIME_TYPES
    clock_skew_seconds: int = 60
    serve_openapi: bool = True

    # -- the identity provider ---------------------------------------------
    #: ``PV_PLATFORM``. The *only* thing that selects a provider.
    platform: PlatformName = "aws"
    #: ``gcp`` only: the Google Cloud / Firebase project id. It is both the
    #: issuer's final segment and the required ``aud``, so one value cannot
    #: be set inconsistently with the other.
    google_project_id: str | None = None
    #: ``local`` only. Never rendered, never logged, never in an error body.
    local_signing_key: bytes | None = field(default=None, repr=False)
    #: Scopes granted to a **logical** client when the token carries none of
    #: its own -- ``provenance-web``, not the opaque id. Empty on Cognito,
    #: where the resource server is authoritative. ``identity_provider_for``
    #: re-keys it onto the opaque ids a provider actually sees, so this side
    #: stays readable and the provider needs no knowledge of the mapping.
    default_scopes_by_client: Mapping[str, frozenset[str]] = field(
        default_factory=lambda: _NO_DEFAULT_SCOPES
    )
    #: Built once in ``__post_init__`` and used both to verify tokens and to
    #: answer ``GET /v1/version``, so the disclosed name is read off the object
    #: actually in force rather than off a parallel string that could drift.
    provider: IdentityProvider = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Validate what a dataclass otherwise would not.

        This exists because of a real defect. ``from_settings`` takes
        ``settings: Any`` -- deliberately, so that importing this module never
        imports ``Settings`` and never reads the environment at import time --
        and ``Any`` makes every attribute read off it unchecked. When
        ``PV_PLATFORM`` widened sixteen AWS fields to ``str | None``,
        ``cognito_issuer=settings.cognito_issuer`` began assigning ``None``
        into a field declared ``str``; ``mypy --strict`` reported clean and a
        frozen dataclass validated nothing, so a ``gcp`` deployment
        constructed happily and failed later, inside a request, during token
        verification. A startup failure had been traded for a runtime one --
        precisely what the platform work existed to prevent.

        ``from_settings`` is not the only constructor, so the invariant lives
        on the type rather than on that one factory.
        """
        from services.control_plane.app.auth.identity import identity_provider_for

        if not isinstance(self.cognito_issuer, str) or not self.cognito_issuer:
            raise ValueError(
                "ApiConfig.issuer must be a non-empty string. This deployment has no "
                "identity provider issuer; it could accept no token, and on a null "
                "issuer it would have accepted one that names no issuer at all."
            )
        if not self.client_id_names:
            raise ValueError(
                "ApiConfig.client_id_names is empty, so every caller resolves to an "
                "unknown app client and reaches no route class."
            )
        if any(not isinstance(k, str) or not k for k in self.client_id_names):
            raise ValueError(
                "ApiConfig.client_id_names has a null or empty key. Three unset app "
                "client ids collapse into a single-entry dict, which reads as a "
                "configured deployment and behaves as an unconfigured one."
            )
        if self.platform == "gcp" and not self.google_project_id:
            raise ValueError("PV_PLATFORM=gcp requires GOOGLE_CLOUD_PROJECT.")
        if self.platform == "local" and not self.local_signing_key:
            raise ValueError("PV_PLATFORM=local requires PV_LOCAL_AUTH_SECRET.")

        provider = identity_provider_for(self)

        # The coordinator's rule, and the right one: a local identity provider
        # outside a local platform is not a state to disclose, it is a state to
        # refuse. Disclosure is for legitimate configurations.
        if provider.name == "local" and self.platform != "local":
            raise ValueError("The local identity provider is only available on PV_PLATFORM=local.")
        if provider.issuer != self.cognito_issuer:
            raise ValueError(
                f"ApiConfig.issuer does not match the {provider.name} provider's issuer. "
                "The value a token is checked against and the value this deployment "
                "believes it published must be one value."
            )
        object.__setattr__(self, "provider", provider)

    @property
    def issuer(self) -> str:
        """The OIDC issuer of the active provider, under a neutral name."""
        return self.cognito_issuer

    @property
    def identity_provider(self) -> IdentityProviderName:
        """What ``GET /v1/version`` discloses.

        Read from :attr:`provider` -- the object that actually verifies
        tokens -- rather than from a configuration field, so the disclosed
        value cannot drift from the verifier in force.
        """
        return self.provider.name

    def logical_client(self, cognito_client_id: str) -> str | None:
        """Map an opaque Cognito ``client_id`` onto the name section 2.4 uses.

        Returning ``None`` for an unknown id is what makes the route-class
        check fail closed: an app client nobody configured reaches no route
        class at all.
        """
        return self.client_id_names.get(cognito_client_id)

    @classmethod
    def from_settings(cls, settings: Any) -> ApiConfig:
        """Build from ``provenance_contracts.settings.Settings``.

        Kept as a classmethod taking ``Any`` so that importing this module
        never imports ``Settings`` -- which would read the environment at
        import time and make the hermetic suites depend on a `.env`. That is
        the right call for import hygiene and the reason ``mypy --strict``
        cannot police this function: every attribute read below is unchecked.
        :meth:`__post_init__` is what makes the unchecked reads safe.

        ``PV_PLATFORM`` now decides which identity block is read. The AWS
        branch is unchanged and still requires everything it always did; what
        changed is that a Google or local deployment is no longer asked for
        Cognito values it will never have, and no longer silently accepts
        their absence.
        """
        platform: PlatformName = getattr(settings, "pv_platform", "aws") or "aws"
        identity = cls._identity_from_settings(settings, platform)

        return cls(
            platform=platform,
            cognito_issuer=identity["issuer"],
            client_id_names=identity["client_id_names"],
            google_project_id=identity["google_project_id"],
            local_signing_key=identity["local_signing_key"],
            default_scopes_by_client=identity["default_scopes_by_client"],
            cursor_hmac_key=settings.cursor_hmac_key.get_secret_value().encode(),
            capability_hmac_key=(
                settings.provenance_capability_hmac_key.get_secret_value().encode()
            ),
            ingest_domain=settings.ses_ingest_domain or DEFAULT_INGEST_DOMAIN,
            git_sha=os.environ.get("GIT_SHA") or settings.build_sha or "unknown",
            region=(
                settings.aws_region
                or getattr(settings, "google_cloud_region", None)
                or DEFAULT_REGION
            ),
            built_at=datetime.now(UTC),
            schema_revision=settings.schema_revision,
            fixture_mode=settings.pv_agent_mode == "FIXTURE",
            agent_mode=settings.pv_agent_mode,
            otlp_export=("ENABLED" if settings.otel_exporter_otlp_endpoint else "DISABLED"),
            judge_group=settings.cognito_judge_group or "provenance-judges",
            max_artifact_bytes=settings.max_artifact_bytes,
        )

    @staticmethod
    def _identity_from_settings(settings: Any, platform: PlatformName) -> dict[str, Any]:
        """The per-platform half of :meth:`from_settings`.

        Every branch either produces a complete identity configuration or
        raises here, on the way up. None of them produces a partial one --
        which is what ``cognito_issuer=None`` was.
        """
        from services.control_plane.app.auth.identity import (
            GOOGLE_SECURETOKEN_ISSUER_PREFIX,
            LOCAL_ISSUER,
        )

        if platform == "aws":
            missing = [
                name
                for name in (
                    "COGNITO_ISSUER",
                    "COGNITO_WEB_CLIENT_ID",
                    "COGNITO_AGENT_CLIENT_ID",
                    "COGNITO_WORKER_CLIENT_ID",
                )
                if not getattr(settings, name.lower(), None)
            ]
            if missing:
                raise ValueError("PV_PLATFORM=aws requires " + ", ".join(missing) + " -- unset.")
            return {
                "issuer": settings.cognito_issuer,
                "client_id_names": _three_clients(
                    settings.cognito_web_client_id,
                    settings.cognito_agent_client_id,
                    settings.cognito_worker_client_id,
                ),
                "google_project_id": None,
                "local_signing_key": None,
                "default_scopes_by_client": _NO_DEFAULT_SCOPES,
            }

        if platform == "gcp":
            project = getattr(settings, "google_cloud_project", None)
            if not project:
                raise ValueError("PV_PLATFORM=gcp requires GOOGLE_CLOUD_PROJECT -- unset.")
            return {
                "issuer": f"{GOOGLE_SECURETOKEN_ISSUER_PREFIX}{project}",
                "client_id_names": _google_client_id_names(settings, project),
                "google_project_id": project,
                "local_signing_key": None,
                "default_scopes_by_client": DEFAULT_SCOPE_GRANTS,
            }

        # ``local``. The secret is read from the process environment rather
        # than from ``Settings``: adding a field there is a change to a file
        # this task does not own, and ``GIT_SHA`` above is the same pattern in
        # the same function. It is required, not defaulted -- a development
        # issuer with a guessable key is a production incident waiting for a
        # misconfigured deploy -- and it is never rendered anywhere.
        secret = os.environ.get("PV_LOCAL_AUTH_SECRET") or ""
        if not secret:
            raise ValueError(
                "PV_PLATFORM=local requires PV_LOCAL_AUTH_SECRET -- unset. It signs the "
                "development issuer's tokens; there is no default, because a default "
                "signing key verifies forged tokens."
            )
        return {
            "issuer": LOCAL_ISSUER,
            "client_id_names": dict(LOCAL_CLIENT_ID_NAMES),
            "google_project_id": None,
            "local_signing_key": secret.encode(),
            "default_scopes_by_client": DEFAULT_SCOPE_GRANTS,
        }


def _default_clock() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Dependencies:
    """Everything the API reaches outward for, in one injectable object.

    ``db_ok`` is a *callable returning a cached bit*, never a query. Section
    8.2: ``/v1/version`` is unauthenticated, and a readiness probe that
    touches CockroachDB on every call is an availability oracle for anyone
    with the URL.
    """

    jwks: JwksProvider
    users: UserDirectory
    capabilities: Any
    idempotency: IdempotencyStore
    read: ReadPort
    write: WritePort
    internal: InternalPort
    clock: Callable[[], datetime] = field(default=_default_clock)
    db_ok: Callable[[], bool] = field(default=lambda: False)
