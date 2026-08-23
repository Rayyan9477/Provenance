"""Authentication and authorisation for the control plane.

`specs/15_API_SPEC.md` sections 2 and 3. Four modules, one job each:

* `jwt` -- verify a Cognito access token (RS256, JWKS, issuer, `token_use`,
  expiry) and return typed claims. The raw token stops here.
* `principal` -- resolve `cognito_sub` to a `Principal` through the `users`
  table, never through a token claim.
* `route_class` -- the `client_id` check that keeps the browser surface and
  the workload surface from leaking into each other. Carries the `G8.8`
  `PV_SABOTAGE` hook.
* `capabilities` / `capability_proof` -- the server-side records that answer
  "acting for whom?" on `/internal/v1`, so no workload ever names a user.
"""

from __future__ import annotations

__all__ = ["capabilities", "capability_proof", "jwt", "principal", "route_class"]
