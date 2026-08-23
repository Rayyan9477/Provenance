"""The identity provider abstraction: who is allowed to say who you are.

Authority: ``specs/15_API_SPEC.md`` sections 2.1-2.5;
``CANONICAL_DECISIONS.md`` -> "Gemini model id canon (frozen 2026-08-24)",
which moves the deployment target from AWS to Google Cloud Run and makes an
AWS-only authentication path a build that cannot be run by anyone outside our
account.

What varies between providers, and what does not
-------------------------------------------------
Almost nothing varies. RS256 over a JWKS is RS256 over a JWKS; the cache, the
``kid`` cooldown, the strict PKCS#1 v1.5 comparison and the refusal ordering in
:mod:`~services.control_plane.app.auth.jwt` are shared verbatim by all three
providers, through :func:`~services.control_plane.app.auth.jwt.verified_payload`.

What varies is the **claim vocabulary** -- which claim carries the subject,
which carries the calling application, which carries scope -- and the
provider-specific replay guard. Each provider below is therefore a claim
mapping and nothing more. None of them may relax a check; a provider is a
translation, not a policy.

Every security property section 2 asserts survives the abstraction, and each
one survives for a structural reason rather than by being re-checked:

* **The principal is a database row, never a claim.** No provider here
  produces a :class:`~provenance_contracts.identity.Principal`. They produce
  :class:`~services.control_plane.app.auth.jwt.TokenClaims`, and
  ``principal.build_human_principal`` still resolves ``sub`` through the
  ``users`` table. A provider cannot change that because it cannot reach it.
* **Route classes admit only configured client ids.** Every provider fills
  ``TokenClaims.client_id`` from a claim the *issuer* controls, and
  ``ApiConfig.logical_client`` maps only the three configured values. A fourth
  application -- a fourth Cognito app client, a fourth service account, a
  fourth anything -- lands on ``UNKNOWN_CLIENT``, which is a member of no
  route class and reaches nothing.
* **An unrecognised scope string is dropped, not rejected.** All three route
  scopes through ``jwt.space_delimited_scopes`` and then
  ``principal.known_scopes``, which is where the dropping lives.
* **The ``kid`` amplification mitigation is untouched.** It lives in
  ``CachingJwksProvider``, which every provider names as its key source.

Why the local provider is a platform, not a flag
-------------------------------------------------
``LocalIdentityProvider`` cannot be switched on. There is no
``PV_ALLOW_LOCAL_AUTH``, no ``debug=True``, no environment variable that flips
it. It is reachable only when ``ApiConfig.platform == "local"``, which is read
from ``PV_PLATFORM`` -- the same value that decides which infrastructure
variables ``Settings`` requires. A deployment cannot be on ``gcp`` for storage
and ``local`` for authentication, because there is one value and it is not
per-subsystem. :meth:`ApiConfig.__post_init__` refuses the combination outright
rather than disclosing it, on the principle that disclosure is for legitimate
configurations and that one is not.

And it announces itself. ``GET /v1/version`` carries ``identity_provider``
beside ``fixture_mode``, read from the provider object that actually verifies
tokens rather than from a parallel configuration string that could drift from
it. An auth path that does not say what it is, is the same class of quiet
dishonesty as an undisclosed fixture-mode demo.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from services.control_plane.app.api.errors import ApiError, ErrorCode
from services.control_plane.app.auth.jwt import (
    JwksProvider,
    TokenClaims,
    b64u_decode,
    decode_and_verify,
    require_configured_issuer,
    space_delimited_scopes,
    token_window,
    unauthenticated,
    verified_payload,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from services.control_plane.app.api.config import ApiConfig

__all__ = [
    "COGNITO_ISSUER_TEMPLATE",
    "COGNITO_JWKS_TEMPLATE",
    "GOOGLE_JWKS_URL",
    "GOOGLE_SECURETOKEN_ISSUER_PREFIX",
    "LOCAL_ISSUER",
    "CognitoIdentityProvider",
    "GoogleIdentityProvider",
    "IdentityProvider",
    "IdentityProviderName",
    "LocalIdentityProvider",
    "PlatformName",
    "identity_provider_for",
    "issue_local_token",
]

IdentityProviderName = Literal["cognito", "google", "local"]
PlatformName = Literal["aws", "gcp", "local"]


# --------------------------------------------------------------------------
# Endpoints, transcribed from current documentation rather than from memory
# --------------------------------------------------------------------------

#: ``specs/15_API_SPEC.md`` section 2.3 step 2.
COGNITO_ISSUER_TEMPLATE: Final[str] = "https://cognito-idp.{region}.amazonaws.com/{pool_id}"
COGNITO_JWKS_TEMPLATE: Final[str] = COGNITO_ISSUER_TEMPLATE + "/.well-known/jwks.json"

#: Google Identity Platform / Firebase Auth. Verified 2026-08-24 against
#: https://firebase.google.com/docs/auth/admin/verify-id-tokens and against
#: ``firebase-admin-node``'s ``src/auth/token-verifier.ts``, which is the
#: reference implementation:
#:
#:     iss  must be "https://securetoken.google.com/<projectId>"
#:     aud  must be your Firebase project ID, and must match <projectId>
#:     sub  must be a non-empty string, the uid of the user or device
#:     alg  RS256
#:
#: The Firebase documentation prints the **x509 PEM** endpoint
#: (``https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com``).
#: The verifier in this package consumes JWK ``n``/``e`` pairs, not PEM
#: certificates, so the URL used is the ``jwks_uri`` that
#: ``https://securetoken.google.com/{project}/.well-known/openid-configuration``
#: advertises. It was fetched on 2026-08-24 and returns the ordinary
#: ``{"keys": [{"kty": "RSA", "alg": "RS256", "use": "sig", "kid", "n", "e"}]}``
#: shape that ``CachingJwksProvider`` already parses. Choosing the PEM endpoint
#: would have meant an X.509 parser, in a module whose entire reason for
#: existing is that it has no cryptography dependency.
GOOGLE_SECURETOKEN_ISSUER_PREFIX: Final[str] = "https://securetoken.google.com/"
GOOGLE_JWKS_URL: Final[str] = (
    "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
)

#: ``.invalid`` is reserved by RFC 2606 and can never resolve, so a token
#: minted for the local issuer can never be confused with one from a real
#: authority, and a misconfigured production deployment pointed at this issuer
#: fails rather than silently trusting something.
LOCAL_ISSUER: Final[str] = "https://local.provenance.invalid/"

#: The custom claims Google Identity Platform carries for us. Firebase custom
#: claims are writable **only** through the Admin SDK from a privileged server
#: -- an end user cannot set their own -- which is what makes them usable for
#: the route-class decision at all. They are the GIP equivalent of a Cognito
#: app client id and a Cognito resource-server scope grant, and they are
#: trusted for exactly the same reason: the issuer controls them, not the
#: caller.
GOOGLE_CLIENT_CLAIM: Final[str] = "provenance_client"
GOOGLE_SCOPE_CLAIM: Final[str] = "scope"
GOOGLE_GROUPS_CLAIM: Final[str] = "provenance_groups"


class IdentityProvider(Protocol):
    """One deployment's answer to "who signs the tokens we accept?".

    Deliberately narrow. A provider verifies a token and returns typed claims;
    it never sees the database, never builds a ``Principal``, and never
    decides a route class. Those decisions stay where the tests that assert
    them already point.

    The three attributes are declared as read-only properties so that an
    implementation may satisfy them with either a plain field or a computed
    property -- ``GoogleIdentityProvider`` derives its issuer from the project
    id rather than storing a second copy that could disagree with it.
    """

    @property
    def name(self) -> IdentityProviderName:
        """The value ``GET /v1/version`` discloses."""

    @property
    def issuer(self) -> str:
        """The exact ``iss`` this deployment accepts, and nothing else."""

    @property
    def jwks_url(self) -> str:
        """Where the signing keys come from.

        Read once at wiring time to build the shared ``CachingJwksProvider``;
        never fetched here.
        """

    async def verify(
        self,
        token: str,
        *,
        jwks: JwksProvider,
        now: float,
        leeway_seconds: int,
    ) -> TokenClaims: ...


# --------------------------------------------------------------------------
# AWS
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CognitoIdentityProvider:
    """``PV_PLATFORM=aws``. The behaviour that shipped, unchanged.

    It delegates to :func:`~services.control_plane.app.auth.jwt.decode_and_verify`
    rather than reimplementing the claim checks, so the 78 tests in
    ``tests/auth`` are testing the production path and not a copy of it.
    """

    issuer: str
    jwks_url: str = ""
    name: IdentityProviderName = "cognito"

    async def verify(
        self,
        token: str,
        *,
        jwks: JwksProvider,
        now: float,
        leeway_seconds: int,
    ) -> TokenClaims:
        return await decode_and_verify(
            token,
            jwks=jwks,
            issuer=self.issuer,
            now=now,
            leeway_seconds=leeway_seconds,
        )


# --------------------------------------------------------------------------
# Google
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GoogleIdentityProvider:
    """``PV_PLATFORM=gcp``. Google Identity Platform / Firebase Auth.

    The one substantive difference from Cognito, stated plainly
    ---------------------------------------------------------
    Cognito issues two token types and section 2.3 step 5 refuses the wrong
    one: ``token_use != "access"`` is ``401`` with
    ``reason = "ID_TOKEN_NOT_ACCEPTED"``. That check is not portable, because
    **Google Identity Platform issues no access token for a first-party API**.
    The ID token is the credential; there is no other one to prefer.

    Dropping the check without replacing it would be a real weakening, so it is
    replaced rather than dropped. What step 5 actually buys is *audience
    binding*: a refusal to accept a token that some other relying party was the
    intended audience of. GIP states that guarantee directly --

        ``aud`` must be your Firebase project ID, and ``iss`` must be
        ``https://securetoken.google.com/<projectId>`` with the same
        ``<projectId>``

    -- so :attr:`audience` is checked for exact equality with the project id
    embedded in :attr:`issuer`, and the two are derived from one configured
    value so they cannot be set inconsistently. A token minted for a different
    Google project fails, which is the property step 5 existed to provide.

    ``auth_time`` is verified as well, because the Firebase documentation lists
    it as required and because a token whose authentication instant is in the
    future is malformed however it was produced.

    Where the calling application comes from
    ----------------------------------------
    A GIP token has no ``client_id``. The route-class check needs one, and it
    must come from somewhere the caller cannot influence. Firebase **custom
    claims** are writable only through the Admin SDK from a privileged server
    environment, which makes them the exact analogue of a Cognito app client
    id: issuer-controlled, inside the signature, invisible to the user.

    So ``client_id`` is the ``provenance_client`` custom claim, falling back to
    ``aud`` -- the project id -- when the claim is absent. That fallback is
    what makes an ordinary signed-in user a ``provenance-web`` caller with no
    provisioning ritual, while a workload identity is distinguished by a claim
    only our own server could have written. Either way the value is looked up
    in ``ApiConfig.client_id_names`` and an unconfigured value reaches nothing.
    """

    project_id: str
    #: Section 2.1's scope-allocation table, which on Cognito lives in the
    #: resource server. GIP has no resource server, so the grant is configured
    #: server-side and keyed on the logical client. Still never request data.
    default_scopes: Mapping[str, frozenset[str]] = field(default_factory=dict)
    name: IdentityProviderName = "google"

    @property
    def issuer(self) -> str:
        return f"{GOOGLE_SECURETOKEN_ISSUER_PREFIX}{self.project_id}"

    @property
    def audience(self) -> str:
        """Equal to the project id, and to the issuer's final segment."""
        return self.project_id

    @property
    def jwks_url(self) -> str:
        return GOOGLE_JWKS_URL

    async def verify(
        self,
        token: str,
        *,
        jwks: JwksProvider,
        now: float,
        leeway_seconds: int,
    ) -> TokenClaims:
        _, payload = await verified_payload(token, jwks=jwks)

        # Same order as Cognito, for the same reason: signature, issuer,
        # replay guard, then expiry. Checking expiry first would let the clock
        # alone distinguish "signed by Google but stale" from "not signed by
        # Google".
        require_configured_issuer(payload, self.issuer)

        audience = payload.get("aud")
        if not isinstance(audience, str) or audience != self.audience:
            raise ApiError(ErrorCode.TOKEN_INVALID_SIGNATURE, details={"reason": "WRONG_AUDIENCE"})

        issued_at, not_before, expires_at = token_window(
            payload, now=now, leeway_seconds=leeway_seconds
        )

        auth_time = payload.get("auth_time")
        if isinstance(auth_time, int | float) and now + leeway_seconds < auth_time:
            raise unauthenticated("AUTH_TIME_IN_THE_FUTURE")

        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject:
            # Firebase: "must be a non-empty string ... the uid of the user".
            raise unauthenticated("MISSING_SUBJECT")

        raw_client = payload.get(GOOGLE_CLIENT_CLAIM)
        client_id = raw_client if isinstance(raw_client, str) and raw_client else audience

        scopes = space_delimited_scopes(payload.get(GOOGLE_SCOPE_CLAIM))
        if not scopes:
            scopes = self.default_scopes.get(client_id, frozenset())

        groups = payload.get(GOOGLE_GROUPS_CLAIM) or []

        return TokenClaims(
            issuer=self.issuer,
            client_id=client_id,
            scopes=scopes,
            sub=subject,
            groups=frozenset(str(g) for g in groups) if isinstance(groups, list) else frozenset(),
            issued_at=issued_at,
            not_before=not_before,
            expires_at=expires_at,
        )


# --------------------------------------------------------------------------
# Local
# --------------------------------------------------------------------------


def _hs256(signing_input: bytes, key: bytes) -> bytes:
    return hmac.new(key, signing_input, hashlib.sha256).digest()


@dataclass(frozen=True, slots=True)
class LocalIdentityProvider:
    """``PV_PLATFORM=local``. A development issuer, so a judge needs no cloud
    identity account to run the system end to end.

    This is **not** an authentication bypass and was deliberately not written
    as one. There is no "trust the token", no unsigned mode, no header that
    names a user. It is a real issuer whose keys happen to live in this
    deployment's own environment: a token must be signed, in date, carry a
    configured ``client_id`` and resolve to a ``users`` row, exactly as on the
    other two platforms. Everything downstream -- route classes, capability
    binding, the database principal lookup -- is byte-for-byte the shared path.

    Why HS256 here and RS256 everywhere else
    -----------------------------------------
    A local issuer has to be able to *mint*, not only verify, and this package
    carries no cryptography dependency on purpose (see :mod:`jwt`'s docstring):
    generating an RSA key in pure Python at start-up costs seconds to minutes,
    and committing a fixed private key to the repository is a credential in
    version control, which is how ``D-00-019`` happened.

    An HMAC is signing and verification in four lines with no key generation
    and no committed secret. The classic danger -- algorithm confusion, where
    an attacker re-signs an RS256 token with HS256 using the public key as the
    MAC key -- is closed structurally rather than by vigilance:

    * this verifier accepts **only** ``alg: HS256`` and is unreachable unless
      ``platform == "local"``;
    * ``jwt.verified_payload``, which every non-local provider uses, accepts
      **only** ``alg: RS256``;
    * there is no configuration in which both are live, because one platform
      value selects one provider.

    ``tests/auth/test_identity_providers.py`` asserts the crossing in both
    directions: an HS256 token is refused by Cognito and Google, and an RS256
    token is refused here.
    """

    signing_key: bytes
    default_scopes: Mapping[str, frozenset[str]] = field(default_factory=dict)
    issuer: str = LOCAL_ISSUER
    jwks_url: str = ""
    name: IdentityProviderName = "local"

    async def verify(
        self,
        token: str,
        *,
        jwks: JwksProvider,
        now: float,
        leeway_seconds: int,
    ) -> TokenClaims:
        del jwks  # Symmetric: the key is the deployment's own, not published.
        if not self.signing_key:
            raise ApiError(
                ErrorCode.INTERNAL_ERROR,
                message="The local identity provider has no signing key configured.",
            )

        parts = token.split(".")
        if len(parts) != 3 or not all(parts[:2]):
            raise unauthenticated("MALFORMED_TOKEN")
        header_b64, payload_b64, signature_b64 = parts

        try:
            header = json.loads(b64u_decode(header_b64))
        except Exception as exc:
            raise unauthenticated("MALFORMED_HEADER") from exc
        if not isinstance(header, dict) or header.get("alg") != "HS256":
            raise ApiError(
                ErrorCode.TOKEN_INVALID_SIGNATURE, details={"reason": "UNEXPECTED_ALGORITHM"}
            )

        try:
            signature = b64u_decode(signature_b64)
        except Exception as exc:
            raise ApiError(
                ErrorCode.TOKEN_INVALID_SIGNATURE, details={"reason": "MALFORMED_SIGNATURE"}
            ) from exc
        expected = _hs256(f"{header_b64}.{payload_b64}".encode("ascii"), self.signing_key)
        if not hmac.compare_digest(expected, signature):
            raise ApiError(
                ErrorCode.TOKEN_INVALID_SIGNATURE, details={"reason": "SIGNATURE_MISMATCH"}
            )

        try:
            payload = json.loads(b64u_decode(payload_b64))
        except Exception as exc:
            raise unauthenticated("MALFORMED_PAYLOAD") from exc
        if not isinstance(payload, dict):
            raise unauthenticated("MALFORMED_PAYLOAD")

        require_configured_issuer(payload, self.issuer)

        # Kept from Cognito rather than dropped: the local issuer mints one
        # token type and says so, so a future second type cannot quietly
        # become acceptable here.
        if payload.get("token_use") != "access":
            raise ApiError(
                ErrorCode.TOKEN_INVALID_SIGNATURE, details={"reason": "ID_TOKEN_NOT_ACCEPTED"}
            )

        issued_at, not_before, expires_at = token_window(
            payload, now=now, leeway_seconds=leeway_seconds
        )

        client_id = payload.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise unauthenticated("MISSING_CLIENT_ID")

        scopes = space_delimited_scopes(payload.get("scope"))
        if not scopes:
            scopes = self.default_scopes.get(client_id, frozenset())

        groups = payload.get("groups") or []
        subject = payload.get("sub")

        return TokenClaims(
            issuer=self.issuer,
            client_id=client_id,
            scopes=scopes,
            sub=str(subject) if subject else None,
            groups=frozenset(str(g) for g in groups) if isinstance(groups, list) else frozenset(),
            issued_at=issued_at,
            not_before=not_before,
            expires_at=expires_at,
        )


def _b64u(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def issue_local_token(claims: Mapping[str, Any], *, key: bytes) -> str:
    """Mint a token the local provider will accept.

    Lives beside the verifier so the development harness and the seed script
    have one to call rather than three hand-rolled JWT builders, and so the
    claim vocabulary has exactly one definition. It is inert without *key*,
    which only a ``PV_PLATFORM=local`` deployment has.
    """
    header = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64u(json.dumps(dict(claims), separators=(",", ":"), sort_keys=True).encode())
    signature = _hs256(f"{header}.{body}".encode("ascii"), key)
    return f"{header}.{body}.{_b64u(signature)}"


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


def _grants_by_client_id(config: ApiConfig) -> Mapping[str, frozenset[str]]:
    """Re-key the scope grant from logical names onto opaque client ids.

    A provider sees ``"provenance-demo"`` -- a Google project id -- in the
    claim; the grant table is written against ``"provenance-web"`` so that a
    person can read it. Exactly one object knows both, and it is the config,
    so the translation happens here rather than inside a provider that would
    then need the route-class vocabulary it has no business holding.
    """
    grants = config.default_scopes_by_client
    return {
        client_id: grants[name]
        for client_id, name in config.client_id_names.items()
        if name in grants
    }


def identity_provider_for(config: ApiConfig) -> IdentityProvider:
    """The one place a provider is chosen, keyed on the platform alone.

    There is no second argument and no override. ``PV_PLATFORM`` decides,
    which is the same value that decides which infrastructure variables
    ``Settings`` requires, so authentication cannot disagree with storage
    about which cloud this deployment is on.
    """
    if config.platform == "local":
        return LocalIdentityProvider(
            signing_key=config.local_signing_key or b"",
            default_scopes=_grants_by_client_id(config),
        )
    if config.platform == "gcp":
        return GoogleIdentityProvider(
            project_id=config.google_project_id or "",
            default_scopes=_grants_by_client_id(config),
        )
    return CognitoIdentityProvider(issuer=config.cognito_issuer)
