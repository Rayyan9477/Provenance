"""The hero dataset canon, asserted against the seed fixtures (``T2.8``).

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> **Hero dataset canon** (frozen 2026-08-17).
  Every name, identifier and date below is quoted from it, never derived from
  the module under test.
- ``docs/specs/10_DATABASE_DDL.md`` sections 17.2 - 17.8.
- ``docs/quality/22_EVAL_DATASETS.md`` section 2 -- ground truth: the seeded
  world, including the per-case seeded revisions.

Why the constants are duplicated here
-------------------------------------
A test that imports ``HERO_USER_NAME`` from the fixture it is checking asserts
only that the fixture equals itself. The register names five counterparties,
six external account references and four dates; a rename in the fixture must
fail here, so the register's values are transcribed as literals.

The one this file exists for
----------------------------
``Kestrel Analytics`` is the **employer**. An earlier design brief made
"Kestrel Moving Co." the mover, which would have attributed the USD 420 damage
claim to the user's employer. ``test_kestrel_is_the_employer_never_the_mover``
is the regression guard for a defect that was caught in review rather than in
code, and it is the reason this file is not merely a spelling check.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.unit

# --- CANONICAL_DECISIONS.md -> Hero dataset canon, transcribed --------------

HERO_USER_DISPLAY_NAME = "Alex Rivera"
HERO_TIMEZONE = "America/New_York"

NORTHLINE_OLD_REF = "NF-4471-8802"
NORTHLINE_NEW_REF = "NF-9913-2250"
HARBORVIEW_REF = "HPM-LEASE-2024-3B"
BELTLINE_REF = "BM-88214"
KESTREL_REF = "KA-EMP-3308"
CASCADE_REF = "CP-770194"

CONTEXT_TITLE = "The Move — 214 Ridgeway to 88 Larkin"

RETIRED_PERSONA = "Dana Whitfield"
RETIRED_MOVER = "Kestrel Moving Co."

#: The landing-screen figure. Harborview 1800.00 + Beltline 220.00; Northline
#: contributes 0 while DISPUTED, because a disputed balance changes ``status``,
#: never ``amount``.
OUTSTANDING_TOTAL = Decimal("2020.00")


def _seed_string_data() -> dict[str, list[str]]:
    """Every string **constant** in ``scripts/seed``, docstrings excluded.

    Scanning raw source would flag the prose in this repository's own
    docstrings -- ``counterparties.py`` explains at length why Kestrel is not
    the mover, and naming the retired name is the whole point of that
    explanation. What must not exist is the retired name as *data*: a
    counterparty display name, a slug, a template string. ``ast`` gives exactly
    that distinction and drops comments for free.
    """
    import ast
    from pathlib import Path as _Path

    seed_dir = _Path(__file__).resolve().parents[4] / "scripts" / "seed"
    out: dict[str, list[str]] = {}
    for path in sorted(seed_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstring_ids: set[int] = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(body, list) or not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstring_ids.add(id(first.value))
        out[path.name] = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_ids
        ]
    return out


# ---------------------------------------------------------------------------
# Tenants and users -- section 17.2
# ---------------------------------------------------------------------------


def test_three_tenants_hero_and_two_isolation() -> None:
    from scripts.seed.tenants import TENANTS

    assert [t.slug for t in TENANTS] == ["hero", "iso-a", "iso-b"]


def test_three_users_one_per_tenant() -> None:
    from scripts.seed.tenants import TENANTS, USERS

    assert len(USERS) == 3
    assert {u.tenant_id for u in USERS} == {t.id for t in TENANTS}


def test_hero_user_is_alex_rivera_in_new_york_with_judge_mode() -> None:
    from scripts.seed.tenants import HERO_USER

    assert HERO_USER.display_name == HERO_USER_DISPLAY_NAME
    assert HERO_USER.timezone == HERO_TIMEZONE
    assert HERO_USER.judge_mode_enabled is True


def test_the_retired_persona_appears_nowhere_in_the_seed() -> None:
    """``Dana Whitfield`` is retired and must not reappear in any example."""
    offenders = [
        f"{name}: {value!r}"
        for name, values in _seed_string_data().items()
        for value in values
        if RETIRED_PERSONA in value
    ]
    assert offenders == []


# ---------------------------------------------------------------------------
# Counterparties and relationships -- section 17.3
# ---------------------------------------------------------------------------


def test_five_counterparties_named_exactly_as_the_register_names_them() -> None:
    from scripts.seed.counterparties import COUNTERPARTIES

    assert sorted(c.display_name for c in COUNTERPARTIES) == [
        "Beltline Movers",
        "Cascade Power",
        "Harborview Property Management",
        "Kestrel Analytics",
        "Northline Fiber",
    ]


def test_counterparty_kinds_match_the_register() -> None:
    from scripts.seed.counterparties import COUNTERPARTIES

    kinds = {c.display_name: c.kind for c in COUNTERPARTIES}
    assert kinds == {
        "Northline Fiber": "ISP",
        "Harborview Property Management": "LANDLORD",
        "Beltline Movers": "MOVING_COMPANY",
        "Kestrel Analytics": "EMPLOYER",
        "Cascade Power": "UTILITY",
    }


def test_kestrel_is_the_employer_never_the_mover() -> None:
    """The defect this whole file exists for.

    An earlier draft made "Kestrel Moving Co." the mover, which would have
    pinned the USD 420 damage claim on the user's employer.
    """
    from scripts.seed.counterparties import COUNTERPARTIES, RELATIONSHIPS

    kestrel = next(c for c in COUNTERPARTIES if c.display_name == "Kestrel Analytics")
    assert kestrel.kind == "EMPLOYER"

    kestrel_rels = [r for r in RELATIONSHIPS if r.counterparty_id == kestrel.id]
    assert [r.relationship_type for r in kestrel_rels] == ["EMPLOYMENT"]

    beltline = next(c for c in COUNTERPARTIES if c.display_name == "Beltline Movers")
    assert beltline.kind == "MOVING_COMPANY"

    offenders = [
        f"{name}: {value!r}"
        for name, values in _seed_string_data().items()
        for value in values
        if RETIRED_MOVER in value
    ]
    assert offenders == []


def test_six_relationships_across_five_counterparties() -> None:
    from scripts.seed.counterparties import COUNTERPARTIES, RELATIONSHIPS

    assert len(COUNTERPARTIES) == 5
    assert len(RELATIONSHIPS) == 6


def test_northline_carries_two_relationships_on_one_counterparty() -> None:
    """The sharpest decoy in the corpus: same counterparty, two accounts."""
    from scripts.seed.counterparties import COUNTERPARTIES, RELATIONSHIPS

    northline = [c for c in COUNTERPARTIES if c.display_name == "Northline Fiber"]
    assert len(northline) == 1
    rels = [r for r in RELATIONSHIPS if r.counterparty_id == northline[0].id]
    assert len(rels) == 2
    assert sorted(r.external_account_ref for r in rels) == sorted(
        [NORTHLINE_OLD_REF, NORTHLINE_NEW_REF]
    )


def test_every_external_account_ref_is_the_frozen_one() -> None:
    from scripts.seed.counterparties import RELATIONSHIPS

    assert sorted(r.external_account_ref for r in RELATIONSHIPS) == sorted(
        [
            NORTHLINE_OLD_REF,
            NORTHLINE_NEW_REF,
            HARBORVIEW_REF,
            BELTLINE_REF,
            KESTREL_REF,
            CASCADE_REF,
        ]
    )


def test_external_account_refs_are_unique() -> None:
    from scripts.seed.counterparties import RELATIONSHIPS

    refs = [r.external_account_ref for r in RELATIONSHIPS]
    assert len(set(refs)) == len(refs)


# ---------------------------------------------------------------------------
# Context and cases -- section 17.4
# ---------------------------------------------------------------------------


def test_one_context_titled_exactly_as_the_register_titles_it() -> None:
    from scripts.seed.cases import CONTEXTS

    assert len(CONTEXTS) == 1
    assert CONTEXTS[0].title == CONTEXT_TITLE
    assert CONTEXTS[0].context_type == "MOVE"


def test_the_move_opened_on_2_april_2026() -> None:
    from scripts.seed.cases import CONTEXTS

    assert CONTEXTS[0].started_at is not None
    assert CONTEXTS[0].started_at.date().isoformat() == "2026-04-02"


def test_ten_cases_inside_the_eight_to_twelve_band() -> None:
    from scripts.seed.cases import CASES

    assert len(CASES) == 10


def test_case_statuses_and_revisions_match_the_eval_ground_truth() -> None:
    """``22_EVAL_DATASETS.md`` section 2 pins a revision per case."""
    from scripts.seed.cases import CASES

    by_slug = {c.slug: c for c in CASES}
    expected = {
        "isp-cancellation": ("RESOLVED", 12),
        "isp-final-bill": ("RESOLVED", 6),
        "landlord-deposit": ("WAITING", 9),
        "landlord-inspection": ("RESOLVED", 4),
        "movers-damage": ("WAITING", 5),
        "movers-scheduling": ("RESOLVED", 3),
        "employer-relocation": ("RESOLVED", 4),
        "employer-stipend": ("RESOLVED", 2),
        "new-install-credit": ("OPEN", 1),
        "final-meter-reading": ("RESOLVED", 2),
    }
    assert set(by_slug) == set(expected)
    for slug, (status, revision) in expected.items():
        assert (by_slug[slug].status, by_slug[slug].revision) == (status, revision), slug


def test_resolved_cases_carry_a_resolved_at() -> None:
    """``ck_cases_resolved_at_consistent`` refuses the alternative."""
    from scripts.seed.cases import CASES

    for case in CASES:
        if case.status == "RESOLVED":
            assert case.resolved_at is not None, case.slug
        else:
            assert case.resolved_at is None, case.slug


def test_four_of_the_six_relationships_are_in_the_move_context() -> None:
    """``G12.1`` asserts the dashboard shows four relationships against a seed
    of six. The plausible reading -- recorded in ``70_TASK_PLAN.md`` section 24
    risk 3 -- is that the dashboard is scoped to "The Move". This pins the seed
    side of that reading so the discrepancy is measurable rather than argued.
    """
    from scripts.seed.cases import CASES, CONTEXTS

    context_id = CONTEXTS[0].id
    in_scope = {c.relationship_id for c in CASES if c.context_id == context_id}
    assert len(in_scope) == 4


# ---------------------------------------------------------------------------
# Curated evidence -- section 17.5
# ---------------------------------------------------------------------------


def test_thirty_two_curated_evidence_items() -> None:
    from scripts.seed.evidence import CURATED_EVIDENCE

    assert len(CURATED_EVIDENCE) == 32


def test_curated_items_per_case_match_section_17_5() -> None:
    from collections import Counter

    from scripts.seed.evidence import CURATED_EVIDENCE

    per_case = Counter(item.case_slug for item in CURATED_EVIDENCE)
    assert dict(per_case) == {
        "isp-cancellation": 7,
        "isp-final-bill": 4,
        "landlord-deposit": 5,
        "landlord-inspection": 3,
        "movers-damage": 5,
        "movers-scheduling": 2,
        "employer-relocation": 3,
        "employer-stipend": 2,
        "new-install-credit": 1,
    }


def test_the_june_invoice_is_deliberately_absent_from_the_seed() -> None:
    """Section 17.5: the USD 186 June invoice is uploaded live during the demo.

    Seeding it would make the reveal a lookup and the counterfactual a fiction.
    """
    from scripts.seed.evidence import CURATED_EVIDENCE

    for item in CURATED_EVIDENCE:
        assert "186" not in item.normalized_text, item.slug


def test_the_june_invoice_artifact_exists_on_disk_but_is_not_seeded() -> None:
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    artifact = repo_root / "demo" / "artifacts" / "northline-june-invoice.eml"
    assert artifact.is_file(), "the live-upload artifact must be committed"
    assert "186.00" in artifact.read_text(encoding="utf-8")


def test_every_curated_item_has_a_real_artifact_on_disk() -> None:
    from pathlib import Path

    from scripts.seed.evidence import CURATED_ARTIFACTS

    repo_root = Path(__file__).resolve().parents[4]
    for artifact in CURATED_ARTIFACTS:
        path = repo_root / "demo" / "artifacts" / artifact.filename
        assert path.is_file(), artifact.filename
        assert path.stat().st_size == artifact.size_bytes, artifact.filename


def test_artifact_hashes_are_the_real_bytes_not_fabricated() -> None:
    import hashlib
    from pathlib import Path

    from scripts.seed.evidence import CURATED_ARTIFACTS

    repo_root = Path(__file__).resolve().parents[4]
    for artifact in CURATED_ARTIFACTS:
        raw = (repo_root / "demo" / "artifacts" / artifact.filename).read_bytes()
        assert hashlib.sha256(raw).digest() == artifact.content_sha256, artifact.filename


def test_curated_evidence_slugs_are_unique() -> None:
    from scripts.seed.evidence import CURATED_EVIDENCE

    slugs = [item.slug for item in CURATED_EVIDENCE]
    assert len(set(slugs)) == len(slugs)


# ---------------------------------------------------------------------------
# Commitments, fulfillments, triggers -- section 17.6
# ---------------------------------------------------------------------------


def test_four_commitments_two_fulfillments_two_triggers() -> None:
    from scripts.seed.obligations import COMMITMENTS, FULFILLMENTS, TRIGGERS

    assert len(COMMITMENTS) == 4
    assert len(FULFILLMENTS) == 2
    assert len(TRIGGERS) == 2


def test_commitment_money_is_decimal_never_float() -> None:
    from scripts.seed.obligations import COMMITMENTS

    for commitment in COMMITMENTS:
        for amount in (
            commitment.committed_amount,
            commitment.fulfilled_amount,
            commitment.outstanding_amount,
        ):
            assert amount is None or isinstance(amount, Decimal), commitment.slug


def test_commitment_arithmetic_satisfies_the_outstanding_identity() -> None:
    """``ck_commitments_outstanding_identity`` refuses anything else."""
    from scripts.seed.obligations import COMMITMENTS

    for commitment in COMMITMENTS:
        if commitment.committed_amount is None:
            continue
        assert (
            commitment.committed_amount - commitment.fulfilled_amount
            == commitment.outstanding_amount
        ), commitment.slug


def test_the_four_commitments_carry_the_frozen_amounts_and_statuses() -> None:
    from scripts.seed.obligations import COMMITMENTS

    by_slug = {c.slug: c for c in COMMITMENTS}
    assert by_slug["deposit"].committed_amount == Decimal("1800.00")
    assert by_slug["deposit"].outstanding_amount == Decimal("1800.00")
    assert by_slug["deposit"].status == "ACTIVE"

    assert by_slug["damage"].committed_amount == Decimal("420.00")
    assert by_slug["damage"].fulfilled_amount == Decimal("200.00")
    assert by_slug["damage"].outstanding_amount == Decimal("220.00")
    assert by_slug["damage"].status == "PARTIAL"

    assert by_slug["relocation"].committed_amount == Decimal("2350.00")
    assert by_slug["relocation"].outstanding_amount == Decimal("0.00")
    assert by_slug["relocation"].status == "FULFILLED"

    assert by_slug["termination"].committed_amount is None
    assert by_slug["termination"].commitment_type == "SERVICE_TERMINATION"
    assert by_slug["termination"].status == "FULFILLED"


def test_outstanding_total_is_2020_with_northline_contributing_zero() -> None:
    """The landing-screen figure, computed from the fixtures.

    Harborview 1800.00 + Beltline 220.00 = 2020.00. Northline's obligation is
    non-monetary and contributes nothing; the June invoice's USD 186 moves
    ``epistemic_status`` to ``DISPUTED`` and never moves an amount.
    """
    from scripts.seed.obligations import COMMITMENTS, outstanding_total

    assert outstanding_total() == OUTSTANDING_TOTAL

    by_slug = {c.slug: c for c in COMMITMENTS}
    northline = by_slug["termination"]
    assert northline.outstanding_amount is None or northline.outstanding_amount == Decimal("0")


def test_fulfilled_amount_equals_the_sum_of_admitted_fulfillments() -> None:
    """Verification query V7, asserted on the fixtures before it is asserted
    on the rows."""
    from scripts.seed.obligations import COMMITMENTS, FULFILLMENTS

    for commitment in COMMITMENTS:
        if commitment.committed_amount is None:
            continue
        admitted = sum(
            (
                f.amount
                for f in FULFILLMENTS
                if f.commitment_slug == commitment.slug and f.admission_status == "ADMITTED"
            ),
            Decimal("0.00"),
        )
        assert admitted == commitment.fulfilled_amount, commitment.slug


def test_the_deposit_trigger_is_armed_on_case_three_with_the_canon_wake() -> None:
    from scripts.seed.ids import TRIGGER_WAKE_AT
    from scripts.seed.obligations import TRIGGERS

    by_slug = {t.slug: t for t in TRIGGERS}
    deposit = by_slug["deposit-overdue"]
    assert deposit.case_slug == "landlord-deposit"
    assert deposit.trigger_type == "COMMITMENT_DEADLINE"
    assert deposit.state == "ARMED"
    assert deposit.not_before == TRIGGER_WAKE_AT
    assert deposit.fired_at is None


def test_the_deposit_predicate_is_the_canon_ast() -> None:
    from scripts.seed.obligations import TRIGGERS

    deposit = next(t for t in TRIGGERS if t.slug == "deposit-overdue")

    # The document is the section 6 ENVELOPE, so `op` sits under `predicate`
    # rather than at the top. Before 2026-08-24 this was a bare AST node and
    # `parse_spec` rejected it outright with UNSUPPORTED_AST_VERSION -- latent
    # only because `prospective_triggers` is empty until the arm path runs.
    assert set(deposit.predicate_ast) == {"ast_version", "bindings", "predicate"}
    assert deposit.predicate_ast["predicate"]["op"] == "AND"

    rendered = repr(deposit.predicate_ast)
    assert "commitments.deposit.outstanding_amount" in rendered
    assert "clock.now" in rendered
    assert "commitments.deposit.due_at" in rendered


def test_trigger_basis_case_revision_matches_its_case() -> None:
    from scripts.seed.cases import CASES
    from scripts.seed.obligations import TRIGGERS

    revisions = {c.slug: c.revision for c in CASES}
    for trigger in TRIGGERS:
        assert trigger.basis_case_revision == revisions[trigger.case_slug], trigger.slug


# ---------------------------------------------------------------------------
# Retraction fixtures -- section 17.8
# ---------------------------------------------------------------------------


def test_three_retraction_fixtures_with_the_frozen_statuses() -> None:
    from scripts.seed.retractions import RETRACTION_FIXTURES

    assert len(RETRACTION_FIXTURES) == 3
    by_slug = {f.slug: f for f in RETRACTION_FIXTURES}
    assert by_slug["isp-wrong-term-date"].retraction_status == "SUPERSEDED"
    assert by_slug["isp-wrong-term-date"].retraction_reason_code == "EXTRACTION_ERROR"
    assert by_slug["movers-350-claim"].retraction_status == "RETRACTED"
    assert by_slug["movers-350-claim"].retraction_reason_code == "USER_CORRECTION"
    assert by_slug["injected-instruction"].retraction_status == "QUARANTINED"
    assert by_slug["injected-instruction"].retraction_reason_code == "ADVERSARIAL_CONTENT"


def test_the_superseding_evidence_is_the_correct_31_may_item() -> None:
    from scripts.seed.retractions import RETRACTION_FIXTURES

    wrong_date = next(f for f in RETRACTION_FIXTURES if f.slug == "isp-wrong-term-date")
    assert "31 July" in wrong_date.normalized_text
    assert wrong_date.retracted_by_slug == "isp-termination-effective-31-may"


def test_every_retraction_fixture_keeps_its_embedding() -> None:
    """Canon item C: retracted evidence retains bytes, metadata and embeddings.

    V11 is the database-side proof; this is the fixture-side one.
    """
    from scripts.seed.retractions import RETRACTION_FIXTURES

    for fixture in RETRACTION_FIXTURES:
        assert fixture.embed is True, fixture.slug


def test_the_injected_instruction_is_retained_not_deleted() -> None:
    from scripts.seed.retractions import RETRACTION_FIXTURES

    injected = next(f for f in RETRACTION_FIXTURES if f.slug == "injected-instruction")
    assert "Ignore previous instructions" in injected.normalized_text
    assert injected.retraction_status == "QUARANTINED"


# ---------------------------------------------------------------------------
# The embedding text template -- 13_RETRIEVAL_SPEC.md section 12.1
# ---------------------------------------------------------------------------


def test_embedding_template_renders_all_six_header_lines_in_order() -> None:
    from scripts.seed.embedding_text import build_embedding_text

    text = build_embedding_text(
        evidence_type="DATE_ASSERTION",
        counterparty_name=None,
        predicate=None,
        valid_from=None,
        valid_to=None,
        currency=None,
        amount=None,
        has_identifier=False,
        normalized_text="body",
    )
    lines = text.splitlines()
    assert lines[0] == "[type=DATE_ASSERTION]"
    assert lines[1] == "[counterparty=unknown]"
    assert lines[2] == "[predicate=unknown]"
    assert lines[3] == "[valid=unknown]"
    assert lines[4] == "[money=none]"
    assert lines[5] == "[has_identifier=false]"
    assert lines[6] == "body"


def test_embedding_template_money_is_two_decimals() -> None:
    from scripts.seed.embedding_text import build_embedding_text

    text = build_embedding_text(
        evidence_type="AMOUNT_ASSERTION",
        counterparty_name="Northline Fiber",
        predicate="amount_due",
        valid_from=None,
        valid_to=None,
        currency="USD",
        amount=Decimal("186"),
        has_identifier=True,
        normalized_text="x",
    )
    assert "[money=USD 186.00]" in text


def test_embedding_template_caps_the_body_at_900_characters() -> None:
    from scripts.seed.embedding_text import MAX_BODY_CHARS, build_embedding_text

    assert MAX_BODY_CHARS == 900
    text = build_embedding_text(
        evidence_type="STATEMENT",
        counterparty_name=None,
        predicate=None,
        valid_from=None,
        valid_to=None,
        currency=None,
        amount=None,
        has_identifier=False,
        normalized_text="x" * 2000,
    )
    assert len(text.splitlines()[6]) == 900


def test_embedding_template_collapses_whitespace() -> None:
    from scripts.seed.embedding_text import build_embedding_text

    text = build_embedding_text(
        evidence_type="STATEMENT",
        counterparty_name=None,
        predicate=None,
        valid_from=None,
        valid_to=None,
        currency=None,
        amount=None,
        has_identifier=False,
        normalized_text="a  \n\t b",
    )
    assert text.splitlines()[6] == "a b"


# ---------------------------------------------------------------------------
# MANIFEST.json -- the committed row-count contract
# ---------------------------------------------------------------------------


def test_manifest_exists_and_covers_all_26_canonical_tables() -> None:
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    manifest = json.loads((repo_root / "db" / "seeds" / "MANIFEST.json").read_text("utf-8"))
    expected = (repo_root / "db" / "expected_tables.txt").read_text("utf-8").split()
    assert len(expected) == 26
    assert sorted(manifest["tables"]) == sorted(expected)


def test_manifest_records_the_demo_anchor_and_the_rng_seed() -> None:
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    manifest = json.loads((repo_root / "db" / "seeds" / "MANIFEST.json").read_text("utf-8"))
    assert manifest["demo_anchor"] == "2026-09-18T09:00:00-04:00"
    assert manifest["rng_seed"] == 20260817
    assert manifest["seed_namespace"] == "6f2b1c40-0000-4000-8000-70726f76656e"


def test_manifest_evidence_count_is_18035_and_hero_scope_is_16035() -> None:
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    manifest = json.loads((repo_root / "db" / "seeds" / "MANIFEST.json").read_text("utf-8"))
    assert manifest["tables"]["evidence_items"] == 18_035
    assert manifest["corpus"]["user_scoped_hero"] == 16_035
    assert manifest["corpus"]["total"] == 18_035


def test_manifest_expected_counts_agree_with_the_fixtures() -> None:
    import json
    from pathlib import Path

    from scripts.seed.cases import CASES, CONTEXTS
    from scripts.seed.counterparties import COUNTERPARTIES, RELATIONSHIPS
    from scripts.seed.evidence import CURATED_ARTIFACTS
    from scripts.seed.tenants import TENANTS, USERS

    repo_root = Path(__file__).resolve().parents[4]
    manifest = json.loads((repo_root / "db" / "seeds" / "MANIFEST.json").read_text("utf-8"))
    tables = manifest["tables"]
    assert tables["tenants"] == len(TENANTS)
    assert tables["users"] == len(USERS)
    assert tables["counterparties"] == len(COUNTERPARTIES)
    assert tables["relationships"] == len(RELATIONSHIPS)
    assert tables["contexts"] == len(CONTEXTS)
    assert tables["cases"] == len(CASES)
    assert tables["source_artifacts"] >= len(CURATED_ARTIFACTS)


def test_manifest_marks_the_kernel_written_tables_as_deferred() -> None:
    """``70_TASK_PLAN.md`` section 24 risk 11.

    The name is kept because ``G2.x`` addresses this test by it, but the
    property it guards is broader than "step 9 is deferred", and step 9 landed
    on 2026-08-24.

    What must never be true is that the manifest claims a **complete** seed
    while the Kernel-written tables are empty. `manifest_check` prints
    ``26 tables checked, 26 match``, and that sentence has to mean something
    different depending on which half of the seed ran. So exactly one of these
    holds, and this test says which:

    * nothing is deferred, and every Kernel-written table carries a non-zero
      expected count -- the replay ran; or
    * something is deferred, and the deferral names those tables **by name**,
      each expected at zero -- the replay did not run, and the manifest says so.

    The failure this forbids is the third state: a cleared deferral over
    tables still expected at zero, which reads as a complete seed and is a
    green light over an unbuilt half.
    """
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    manifest = json.loads((repo_root / "db" / "seeds" / "MANIFEST.json").read_text("utf-8"))
    deferred = manifest["deferred"]
    kernel_written = {
        "claims",
        "beliefs",
        "belief_versions",
        "belief_support",
        "commitments",
        "fulfillments",
        "prospective_triggers",
        "state_transitions",
    }

    if deferred["tables"]:
        # The replay did not run. Every deferred table is named and expected
        # empty, and the reason is stated rather than implied by a zero.
        assert set(deferred["tables"]) >= kernel_written
        assert deferred["reason"].strip(), "a deferral with no reason is an unexplained zero"
        for table in deferred["tables"]:
            assert manifest["tables"][table] == 0, table
        return

    # The replay ran. Nothing may still be expected at zero, or the cleared
    # deferral is claiming a completeness the counts contradict.
    still_empty = sorted(t for t in kernel_written if manifest["tables"][t] == 0)
    assert not still_empty, (
        f"the deferral is cleared but {still_empty} are still expected at zero. "
        "Either step 9 did not populate them or the manifest was cleared without "
        "re-measuring; both make '26 tables checked, 26 match' a false claim."
    )
    assert deferred["reason"].strip(), (
        "even a cleared deferral states why -- a reader finding an empty block "
        "cannot tell 'nothing was deferred' from 'nobody filled this in'"
    )


# ---------------------------------------------------------------------------
# Closed vocabularies -- the CHECK constraints, asserted before the load
# ---------------------------------------------------------------------------
#
# ``CANONICAL_DECISIONS.md`` -> "Closed domain vocabularies": every layer mirrors
# the specification's enum membership exactly, with no layer-local aliases. The
# database refuses anything else, so a fixture carrying a plausible non-member
# fails three minutes into an 18,035-row load rather than at import. Both
# ``REFUND`` and ``REIMBURSEMENT`` read perfectly well and neither exists; they
# were the values these fixtures shipped with until this test was written.

COMMITMENT_TYPES = frozenset(
    {
        "MONETARY_PAYMENT",
        "MONETARY_REFUND",
        "MONETARY_REIMBURSEMENT",
        "MONETARY_CREDIT",
        "DEPOSIT_RETURN",
        "SERVICE_TERMINATION",
        "SERVICE_DELIVERY",
        "REPAIR",
        "RESPONSE",
        "DOCUMENT_DELIVERY",
        "CORRECTION",
        "OTHER",
    }
)

CASE_TYPES = frozenset(
    {
        "SERVICE_CANCELLATION",
        "DEPOSIT_RETURN",
        "DAMAGE_REIMBURSEMENT",
        "EXPENSE_REIMBURSEMENT",
        "BILLING_DISPUTE",
        "WARRANTY_CLAIM",
        "REFUND",
        "ACCOUNT_CLOSURE",
        "SERVICE_INSTALLATION",
        "GENERAL",
    }
)

CASE_STATUSES = frozenset(
    {
        "OPEN",
        "WAITING",
        "ACTIONABLE",
        "IN_PROGRESS",
        "DISPUTED",
        "BLOCKED",
        "AWAITING_USER",
        "RESOLVED",
        "REOPENED",
        "SUPERSEDED",
    }
)

RELATIONSHIP_TYPES = frozenset(
    {
        "SERVICE_ACCOUNT",
        "TENANCY",
        "EMPLOYMENT",
        "VENDOR_ENGAGEMENT",
        "FINANCIAL_ACCOUNT",
        "LOYALTY",
        "INSURANCE_POLICY",
        "OTHER",
    }
)

COUNTERPARTY_KINDS = frozenset(
    {
        "ISP",
        "LANDLORD",
        "MOVING_COMPANY",
        "EMPLOYER",
        "BANK",
        "RETAILER",
        "AIRLINE",
        "UTILITY",
        "INSURER",
        "HEALTHCARE_PROVIDER",
        "GOVERNMENT",
        "TELECOM",
        "OTHER",
    }
)

EVIDENCE_TYPES = frozenset(
    {
        "STATEMENT",
        "CONFIRMATION",
        "CANCELLATION_NOTICE",
        "SERVICE_STATUS_ASSERTION",
        "INVOICE_LINE",
        "PAYMENT_RECORD",
        "RECEIPT",
        "COMMITMENT_STATEMENT",
        "POLICY_TERM_TEXT",
        "DATE_ASSERTION",
        "AMOUNT_ASSERTION",
        "IDENTIFIER_ASSERTION",
        "ADDRESS_ASSERTION",
        "CORRECTION_NOTICE",
        "ATTACHMENT_REFERENCE",
        "QUOTED_HISTORY_EXCERPT",
    }
)

TRIGGER_TYPES = frozenset(
    {"COMMITMENT_DEADLINE", "RESPONSE_DEADLINE", "CONFLICT_TIMEOUT", "WARRANTY_WINDOW"}
)

RETRACTION_REASON_CODES = frozenset(
    {
        "USER_CORRECTION",
        "EXTRACTION_ERROR",
        "SOURCE_WITHDRAWN",
        "DUPLICATE_OF_OTHER",
        "PARSER_DEFECT",
        "ADVERSARIAL_CONTENT",
    }
)


def test_every_commitment_type_is_a_member_of_the_closed_vocabulary() -> None:
    from scripts.seed.obligations import COMMITMENTS

    used = {c.commitment_type for c in COMMITMENTS}
    assert used <= COMMITMENT_TYPES, sorted(used - COMMITMENT_TYPES)


def test_every_case_type_and_status_is_a_member() -> None:
    from scripts.seed.cases import CASES

    assert {c.case_type for c in CASES} <= CASE_TYPES
    assert {c.status for c in CASES} <= CASE_STATUSES


def test_every_relationship_type_and_counterparty_kind_is_a_member() -> None:
    from scripts.seed.counterparties import COUNTERPARTIES, RELATIONSHIPS

    assert {r.relationship_type for r in RELATIONSHIPS} <= RELATIONSHIP_TYPES
    assert {c.kind for c in COUNTERPARTIES} <= COUNTERPARTY_KINDS


def test_every_evidence_type_is_a_member() -> None:
    from scripts.seed.decoys import generate_decoys
    from scripts.seed.evidence import CURATED_EVIDENCE
    from scripts.seed.retractions import RETRACTION_FIXTURES

    used = {e.evidence_type for e in (*CURATED_EVIDENCE, *RETRACTION_FIXTURES)}
    used |= {d.evidence_type for d in generate_decoys()}
    assert used <= EVIDENCE_TYPES, sorted(used - EVIDENCE_TYPES)


def test_every_trigger_type_and_retraction_reason_is_a_member() -> None:
    from scripts.seed.obligations import TRIGGERS
    from scripts.seed.retractions import RETRACTION_FIXTURES

    assert {t.trigger_type for t in TRIGGERS} <= TRIGGER_TYPES
    assert {
        f.retraction_reason_code for f in RETRACTION_FIXTURES if f.retraction_reason_code
    } <= RETRACTION_REASON_CODES


def test_every_source_type_and_mime_is_a_member() -> None:
    from scripts.seed.decoys import generate_decoys
    from scripts.seed.world import curated_artifacts

    source_types = {
        "EMAIL_INBOUND",
        "UPLOAD_EML",
        "UPLOAD_PDF",
        "UPLOAD_IMAGE",
        "UPLOAD_TEXT",
        "USER_CORRECTION",
        "SEED_FIXTURE",
    }
    mimes = {"message/rfc822", "application/pdf", "image/png", "image/jpeg", "text/plain"}
    artifacts = [*curated_artifacts(), *(d.to_artifact() for d in generate_decoys())]
    assert {a.source_type for a in artifacts} <= source_types  # type: ignore[attr-defined]
    assert {a.mime_type for a in artifacts} <= mimes  # type: ignore[attr-defined]


def test_every_s3_key_matches_the_shape_check() -> None:
    """``ck_source_artifacts_s3_key_shape``: ``s3_key LIKE 'raw/%'``."""
    from scripts.seed.decoys import generate_decoys
    from scripts.seed.world import curated_artifacts

    artifacts = [*curated_artifacts(), *(d.to_artifact() for d in generate_decoys())]
    bad = [a.s3_key for a in artifacts if not a.s3_key.startswith("raw/")]  # type: ignore[attr-defined]
    assert bad == []


def test_manifest_pins_the_vector_cache_by_content() -> None:
    """``10_DATABASE_DDL.md`` section 20 risk 10 -- "commit its manifest hash".

    The digest is over the sorted ``(key, float32 bytes)`` pairs, not over the
    parquet file: parquet bytes move with the pyarrow version and the
    compression codec, so a colleague who regenerated an identical cache would
    get a different file hash and read it as corruption. What has to be
    identical for an eval number to be comparable is the vectors.

    Skipped rather than failed when the cache is absent: a fresh clone that has
    not run the seed legitimately has no cache, and ``build_manifest`` records
    ``null`` in that case.
    """
    import json
    from pathlib import Path

    from scripts.seed.embeddings import CACHE_PATH, VectorCache

    repo_root = Path(__file__).resolve().parents[4]
    manifest = json.loads((repo_root / "db" / "seeds" / "MANIFEST.json").read_text("utf-8"))
    recorded = manifest["embedding"]["cache_content_sha256"]
    if not CACHE_PATH.is_file():
        pytest.skip("db/seeds/vectors.parquet is absent; nothing to pin")
    cache = VectorCache().load()
    assert manifest["embedding"]["cache_vectors"] == len(cache)
    assert recorded == cache.content_sha256()
