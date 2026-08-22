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

Built, against the contract rather than against a server. All fourteen screens
render; `npm run verify` is green; nothing has been deployed.

Phase 8's control plane does not exist yet, so this application runs in one of
two modes and is explicit on screen about which:

| Mode | Condition | Disclosure |
|---|---|---|
| `LIVE` | `PV_API_BASE_URL` is set | none needed |
| `FIXTURE` | it is not | a non-dismissible banner on every screen |

There is no third mode, and in particular none in which a fixture is served
without saying so. When Phase 8 lands, set `PV_API_BASE_URL`; no rendering code
changes, because no component has ever seen a fixture.

## The rule this codebase is built around

**Nothing renders that has no backing row.** No hard-coded id, no fixture
masquerading as live data, no placeholder that looks like a value. Where data is
unavailable the UI says so; it does not invent a plausible number.

The rule is enforced mechanically rather than by review, because it is easy to
state and easy to violate by accident. `scripts/check-render-honesty.mjs` runs
first in `npm run build` and applies six rules:

| | |
|---|---|
| `R1` | fixtures may be imported only by tests and the one declared adapter |
| `R2` | no identifier literals in rendering source (`G12.3`, verbatim) |
| `R3` | no hero-canon values typed into `src/app`, `src/components`, `src/lib` |
| `R4` | the absence glyph belongs to `<Absent />` alone |
| `R5` | everything under `src/fixtures` is named `*.fixture.ts` |
| `R6` | a missing value may not fall back to something that reads as a value |

`npm run honesty:counterfactual` is the proof the checker is load-bearing: it
plants one violation per rule in a throwaway tree and exits non-zero unless all
six fire. `src/lib/__tests__/render-honesty.test.ts` binds both to the suite.

## Scripts

```
npm run verify     honesty + counterfactual + typecheck + lint + format + test
npm run build      honesty, then next build
npm test           vitest run
```

`make run-web` still reports the owning phase; Phase 13 deploys this unit.
