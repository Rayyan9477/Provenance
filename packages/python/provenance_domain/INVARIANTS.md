# Invariant → enforcing function → proving test

`23_PHASE_GATES.md` §23.15 requires that every invariant name the test that proves
it. "We have 285 tests" is not reportable evidence about an invariant; this map is.

`python -m tools.invariant_map_check` parses the table below, imports every named
function, collects every named test, and reports `UNPROVEN` for any invariant whose
test is **missing, skipped, or xfailed**. A skipped test counts as unproven — that is
the entire point, and it is why the tool exists rather than a grep.

Five rows: the four canon invariants from `00_PRODUCT.md` §0.1, plus **grounding**,
which is listed separately because it is the one an LLM-authored belief violates most
easily and the one Judge Mode renders directly.

The `file:line` columns are checked against the real definitions, so this table cannot
drift silently as the modules move. It is generated from the source rather than typed.

| Invariant | Enforcing function | Defined at | Proving test | Test at |
|---|---|---|---|---|
| Evidence is append-only | `provenance_domain.invariants.evidence_change_is_append_only`<br>`provenance_domain.invariants.assert_evidence_append_only` | `packages/python/provenance_domain/src/provenance_domain/invariants.py:406`<br>`packages/python/provenance_domain/src/provenance_domain/invariants.py:487` | `test_invariant_1_evidence_is_append_only`<br>`test_append_only_allows_only_the_retraction_status_block`<br>`test_append_only_refuses_unretraction_identity_change_and_self_retraction` | `packages/python/provenance_domain/tests/test_invariants.py:152`<br>`packages/python/provenance_domain/tests/test_invariants.py:202`<br>`packages/python/provenance_domain/tests/test_invariants.py:255` |
| Beliefs are revisable | `provenance_domain.invariants.belief_revision_verdict`<br>`provenance_domain.invariants.assert_belief_revisable` | `packages/python/provenance_domain/src/provenance_domain/invariants.py:531`<br>`packages/python/provenance_domain/src/provenance_domain/invariants.py:611` | `test_invariant_2_beliefs_are_revisable`<br>`test_revision_requires_predecessor_and_supersession_reason` | `packages/python/provenance_domain/tests/test_invariants.py:308`<br>`packages/python/provenance_domain/tests/test_invariants.py:344` |
| State is transactional | `provenance_domain.invariants.derive_outstanding`<br>`provenance_domain.invariants.assert_commitment_consistent`<br>`provenance_domain.invariants.assert_revision_increment` | `packages/python/provenance_domain/src/provenance_domain/invariants.py:194`<br>`packages/python/provenance_domain/src/provenance_domain/invariants.py:258`<br>`packages/python/provenance_domain/src/provenance_domain/invariants.py:276` | `test_invariant_3_state_is_transactional`<br>`test_derive_outstanding_calls_money_outstanding_through_the_module_global`<br>`test_assert_revision_increment_moves_by_exactly_one` | `packages/python/provenance_domain/tests/test_invariants.py:385`<br>`packages/python/provenance_domain/tests/test_invariants.py:499`<br>`packages/python/provenance_domain/tests/test_invariants.py:656` |
| Actions are permissioned | `provenance_domain.invariants.assert_action_permissioned` | `packages/python/provenance_domain/src/provenance_domain/invariants.py:675` | `test_invariant_4_actions_are_permissioned`<br>`test_action_execution_binds_to_draft_hash_and_case_revision` | `packages/python/provenance_domain/tests/test_invariants.py:684`<br>`packages/python/provenance_domain/tests/test_invariants.py:719` |
| Grounding | `provenance_domain.invariants.grounding_verdict`<br>`provenance_domain.invariants.assert_grounded` | `packages/python/provenance_domain/src/provenance_domain/invariants.py:767`<br>`packages/python/provenance_domain/src/provenance_domain/invariants.py:858` | `test_grounding_invariant_holds_for_evidence_and_derivation`<br>`test_grounding_refuses_an_unregistered_or_unversioned_derivation` | `packages/python/provenance_domain/tests/test_invariants.py:754`<br>`packages/python/provenance_domain/tests/test_invariants.py:798` |

## What is *not* claimed here

These are **pure-function** proofs over in-memory inputs. They establish that the
predicates are correct, not that the database enforces them:

- Invariant 1 is enforced in production by a `BEFORE UPDATE` trigger and by grants
  that deny `UPDATE` on `evidence_items` outside the retraction status block. That is
  Phase 2 and Phase 11 work, and it is not exercised here.
- Invariant 3 is enforced by a single serializable transaction in the Memory Kernel.
  `assert_revision_increment` proves only the arithmetic; the atomicity is Phase 4.
- Invariant 4 is enforced by capability checks at the API boundary and by the executor
  refusing a stale `basis_case_revision`. That is Phase 9.

Until those phases land, this map proves the **rules**, and `G-0`'s standing question
Q3 answer — "all five invariants are unproven at the database level" — remains correct.
