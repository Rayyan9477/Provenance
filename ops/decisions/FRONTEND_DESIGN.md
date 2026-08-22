# Frontend design — received, and checked against canon

**Status:** received 2026-08-18. **Consumed at Phase 12 (T12.1 onward), not before.**

## What arrived

| Artefact | Location |
|---|---|
| Rendered design, 26 pages | `Design.pdf` (repository root) |
| Source project | Claude Design project `e3f732ac-1dc8-4e17-9a49-db084bca0ece`, files `Provenance.dc.html` and `support.js` |

Produced from `docs/frontend/33_DESIGN_PROTOTYPE_PROMPT.md`, the standalone paste-ready brief.

## Canon check — done on receipt, not deferred to Phase 12

A returned design that quietly contradicts the seed is expensive to discover during
implementation, because by then the contradiction is spread across components. Checked
against `CANONICAL_DECISIONS.md` → *Hero dataset canon*:

| Canon value | Design | |
|---|---|---|
| Hero user **Alex Rivera**, `America/New_York` | `AR · Alex Rivera · America/New_York` | ✅ |
| **Northline Fiber**, relationship `NF-4471-8802` | `Northline Fiber ••••8802`, `case_id=NF-4471-8802` | ✅ |
| **Harborview Property Management**, deposit `due_at 2026-06-15` | `DUE 15 JUN 2026`, `commitment=CMT-208` | ✅ |
| **Beltline Movers**, USD 220.00 | `BELTLINE MOVERS USD 220.00` | ✅ |
| Demo clock **2026-09-18**, 95 days overdue | `18 SEP 2026 · 14:05 UTC`, `95 DAYS PAST PROMISED DATE` | ✅ |
| Case `RESOLVED → REOPENED`, revision **13** | `REOPENED`, `REVISION 13`, `SETTLED AT REV 13` | ✅ |
| Balance stays **USD 0** while status becomes `DISPUTED` | `TOTAL OUTSTANDING USD 2,020.00` = 1,800 + 220; Northline contributes **0** and reads `status=DISPUTED` | ✅ |
| Judge Mode and Counterfactual are first-class surfaces | screens 11 and 12 of 14 | ✅ |

**The outstanding total is the one worth stating explicitly**, because it is the number a
hostile judge checks first. USD 2,020.00 is Harborview 1,800.00 plus Beltline 220.00.
Northline is in the list and contributes nothing, which is exactly the invariant: a
disputed balance changes `status`, never `amount`. A design that had shown Northline's
disputed figure inside the total would have contradicted the kernel on the landing screen.

## What the design commits us to

Both are already specified; recording them here so Phase 12 does not rediscover them.

- **Bitemporality is rendered, not implied.** Every attention row carries `VALID TIME` and
  `RECORD TIME` as separate labelled fields (`ADMITTED 18 SEP 2026, 14:05 UTC` versus
  `1 JUN – 30 JUN 2026`). `StateProof` must therefore surface both, and the API must return
  both on every timeline entry.
- **The `PROSE / TYPED RECORD` toggle is a disclosure control, not a theme.** Typed record
  shows raw field-level state — `belief=balance_owed v2 status=DISPUTED confidence=0.7100`,
  `trigger=TRG-64 state=FIRED woke_on=ELAPSED_TIME user_reminders=0`. Every one of those
  tokens must come from a real row. This is the strongest honesty commitment in the design
  and the easiest to fake, so `G12.x` render-honesty assertions apply to it in full: no
  hard-coded id, no fixture value, nothing renderable without a backing row.

## Not yet done

- `support.js` has not been read. Read it at T12.1, together with `Provenance.dc.html`.
- The **claude.ai design MCP** (`DesignSync`) requires an interactive `/design-login`
  authorization. This session is non-interactive and cannot run the OAuth flow, so the
  project was reviewed from the exported PDF. Authorize the connector before T12.1 if the
  live project is to be synced rather than re-exported.
- No frontend code exists. Phase 12 depends on G-8 (API) and G-11 (MCP), neither started.
