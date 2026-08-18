# `web` — deployment unit 1 of 4

Next.js + TypeScript UI on Amplify Hosting.

`implementation/00_IMPLEMENTATION_MAP.md` §4.2 fixes the deployment units at
exactly four: **`web`**, `control-plane`, `agent-runtime`, `workers`. Provenance
is a modular monolith plus managed async workers, not a microservice zoo.
`ARCHITECTURE.md` §25, which specified five services and three agent services,
is **superseded** and must not be built from.

Do not add a fifth deployment unit. If a new concern seems to need one, it
belongs inside one of these four.

## What this unit owns

The user-facing surface: the case list, the State Proof view, the approval
flow, and Judge Mode with its counterfactual panel. Specifications:

- `frontend/30_UX_SPEC.md` — screens and interaction
- `frontend/32_JUDGE_MODE.md` — Judge Mode and the counterfactual parity block

## What this unit must never do

- Render a hard-coded object identifier or a scripted trace animation. Judge
  Mode is built from persisted runtime rows and spans, and `G12.4` breaks the
  database to prove the UI moves with it.
- Render `corpus_size_user_scoped` or `corpus_size_visible` as a constant. Both
  are counted at query time.
- Render the two counterfactual output columns when `parity.all_equal` is
  `false`. A failure banner replaces them.
- Read `fixture_mode` from anywhere but `GET /v1/version` (mirrored by
  `GET /v1/me.feature_flags.fixture_mode`). `/v1/healthz` is a bare liveness
  probe and never carries it.

## Status

Not yet built. Phase 12 (`T12.1` onward) creates this application; Phase 13
deploys it. `make run-web` reports the owning phase until then.
