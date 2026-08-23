"""The rejection matrix — money, time, confidence, intervals, schema version.

Written before ``base.py``, ``identity.py``, ``ingestion.py``, ``retrieval.py``,
``resolution.py`` and ``predicates.py`` exist (T1.5).

Authority
---------
- ``specs/11_CONTRACTS.md`` section 6 (``base.py``), section 7 (``identity.py``),
  section 8 (``ingestion.py``), section 11 (``predicates.py``) and section 20.3,
  which prints the first version of this file.
- ``specs/11_CONTRACTS.md`` section 23, risk 1: "**Required before the first
  commit of real money data:** a test that asserts the exact behaviour of
  ``Money.model_validate_json('{"amount": 186.00, ...}')`` on the pinned
  pydantic version". :func:`test_money_rejects_a_bare_json_number` is that test.
- ``quality/23_PHASE_GATES.md`` gate ``G1.5``, which runs

      pytest packages/python/provenance_contracts/tests/test_scalars.py -q -k reject

  and requires that selection to cover **seven** rejections: negative
  confidence, confidence > 1, float amount, naive datetime,
  ``valid_to <= valid_from``, a 4-letter currency, and a missing
  ``schema_version``. Every test name below that carries ``reject`` is part of
  that selection, so a rename is a gate change.
- ``EXECUTION/70_TASK_PLAN.md`` T1.5.
- ``CANONICAL_DECISIONS.md`` -> "Trigger arithmetic": no general arithmetic
  nodes in the trigger DSL. :func:`test_predicate_ast_rejects_arithmetic_ops`
  is the executable form of that decision.

The seven G1.5 rejections, by test name
---------------------------------------
1. negative confidence      -> ``test_confidence_rejects_a_negative_value``
2. confidence > 1           -> ``test_confidence_rejects_a_value_above_one``
3. float amount             -> ``test_money_rejects_a_float_amount``
4. naive datetime           -> ``test_utc_datetime_rejects_a_naive_value``
5. valid_to <= valid_from   -> ``test_half_open_interval_rejects_valid_to_at_or_before_valid_from``
6. 4-letter currency        -> ``test_money_rejects_a_four_letter_currency``
7. missing schema_version   -> ``test_boundary_contract_rejects_a_missing_schema_version``

Recorded deviation: what "missing ``schema_version``" can mean
--------------------------------------------------------------
``EXECUTION/70_TASK_PLAN.md`` T1.5 asks for ``schema_version`` to be a
*required* field. ``specs/11_CONTRACTS.md`` section 6 declares it with a
default of ``SCHEMA_VERSION``, and section 20.9 asserts on that default
(``model.model_fields["schema_version"].default == SCHEMA_VERSION``). The spec
outranks the task plan, so the field keeps its default and a *constructor* that
omits it is filled in rather than refused. What is refused is a payload that
carries the field with no usable value — ``null``, empty, malformed, or a major
this build does not understand — which is the form the failure actually takes
on a wire, in a queue, or in a persisted ``memory_proposals.payload`` row.
Test 7 asserts every one of those. The discrepancy is reported, not resolved
here.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import ModuleType
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from provenance_contracts import base, identity, ingestion, predicates, resolution, retrieval
from provenance_contracts.base import (
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_MAJORS,
    BoundaryContract,
    Confidence,
    Contract,
    HalfOpenInterval,
    Money,
    UtcDatetime,
    canonical_json,
    content_hash,
    new_id,
)
from provenance_contracts.identity import InternalPrincipal, Principal
from provenance_contracts.ingestion import ContentBlock, EvidenceCandidate, SourceLocator
from provenance_contracts.predicates import MAX_PREDICATE_DEPTH, PredicateNode
from provenance_contracts.resolution import TemporalInterpretation
from provenance_contracts.retrieval import EvidenceSnippet, TemporalFact
from provenance_domain.enums import (
    ContentBlockKind,
    DateGranularity,
    EvidenceType,
    Modality,
    PredicateOp,
    SourceClass,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures and probes
#
# No wall-clock reads (20_TDD_STRATEGY.md section 4.2 rule 3): every instant
# below is a literal, and every one of them is timezone-aware because the
# product refuses naive datetimes at every boundary.
# ---------------------------------------------------------------------------

JUNE_1 = datetime(2026, 6, 1, tzinfo=UTC)
JUNE_5 = datetime(2026, 6, 5, tzinfo=UTC)
JULY_1 = datetime(2026, 7, 1, tzinfo=UTC)

SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

SIX_MODULES: tuple[ModuleType, ...] = (
    base,
    identity,
    ingestion,
    retrieval,
    resolution,
    predicates,
)


class _Probe(BaseModel):
    """A plain pydantic model, so the scalar types are tested in isolation."""

    at: UtcDatetime
    score: Confidence


class _BoundaryProbe(BoundaryContract):
    """The smallest thing that crosses a boundary."""

    value: str = "x"


def _text_span(block_id: str = "blk_body1") -> SourceLocator:
    return SourceLocator(kind="TEXT_SPAN", block_id=block_id, char_start=0, char_end=64)


def _evidence_candidate(**overrides: Any) -> EvidenceCandidate:
    payload: dict[str, Any] = {
        "local_id": "ev_1",
        "evidence_type": EvidenceType.INVOICE_LINE,
        "exact_text": "Amount due USD 186.00 for service June 1 - June 30.",
        "normalized_text": "amount due usd 186.00 for service june 1 - june 30",
        "block_id": "blk_body1",
        "source_locator": _text_span(),
        "source_class": SourceClass.PROVIDER_SYSTEM_NOTICE,
        "modality": Modality.ASSERTED_PRESENT,
        "observed_at": JUNE_5,
        "extraction_confidence": "0.92",
    }
    payload.update(overrides)
    return EvidenceCandidate(**payload)


def _evidence_snippet(**overrides: Any) -> EvidenceSnippet:
    payload: dict[str, Any] = {
        "evidence_id": uuid.uuid4(),
        "artifact_id": uuid.uuid4(),
        "evidence_type": EvidenceType.INVOICE_LINE,
        "normalized_text": "Invoice for service June 1 through June 30. Amount due USD 186.",
        "source_locator": _text_span(),
        "observed_at": JUNE_5,
    }
    payload.update(overrides)
    return EvidenceSnippet(**payload)


def _temporal_fact(**overrides: Any) -> TemporalFact:
    payload: dict[str, Any] = {
        "label": "Service period on the June invoice",
        "predicate": "service.period",
        "recorded_at": JUNE_5,
    }
    payload.update(overrides)
    return TemporalFact(**payload)


def _temporal_interpretation(**overrides: Any) -> TemporalInterpretation:
    payload: dict[str, Any] = {
        "target_kind": "CLAIM",
        "target_local_id": "cl_1",
        "granularity": DateGranularity.DAY,
        "basis": "The invoice names a service period explicitly.",
        "confidence": "0.90",
    }
    payload.update(overrides)
    return TemporalInterpretation(**payload)


#: Every builder that produces a contract carrying the ``[valid_from, valid_to)``
#: pair. Parametrising over the builders rather than asserting on one model is
#: what stops the rule from being enforced in the one place a test looked.
INTERVAL_BUILDERS: tuple[tuple[str, Any], ...] = (
    ("HalfOpenInterval", lambda **kw: HalfOpenInterval(**kw)),
    ("EvidenceCandidate", _evidence_candidate),
    ("EvidenceSnippet", _evidence_snippet),
    ("TemporalFact", _temporal_fact),
    ("TemporalInterpretation", _temporal_interpretation),
)


# ---------------------------------------------------------------------------
# L2 — money is Decimal, never float
# ---------------------------------------------------------------------------


def test_money_rejects_a_float_amount() -> None:
    """G1.5 rejection 3. Binary rounding may not enter an obligation."""
    with pytest.raises(ValidationError) as excinfo:
        Money(amount=186.00, currency="USD")
    assert "float is not an acceptable monetary amount" in str(excinfo.value)


def test_money_rejects_a_bool_amount() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Money(amount=True, currency="USD")
    assert "bool is not a monetary amount" in str(excinfo.value)


def test_money_rejects_a_bare_json_number() -> None:
    """``specs/11_CONTRACTS.md`` section 23, risk 1 — measured, not assumed.

    A hand-written client posting ``{"amount": 186.00}`` must be refused just
    as a Python ``float`` is. The wire form of money is a JSON *string*
    (section 6.1); a bare number has already passed through a parser that may
    have reinterpreted it as a binary float, and there is no way to tell after
    the fact whether it did.
    """
    with pytest.raises(ValidationError):
        Money.model_validate_json('{"amount": 186.00, "currency": "USD"}')


def test_money_rejects_more_than_four_decimal_places() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Money(amount="186.000001", currency="USD")
    assert "more than 4 decimal places" in str(excinfo.value)


def test_money_rejects_a_four_letter_currency() -> None:
    """G1.5 rejection 6. ISO-4217 alpha-3, upper case, nothing else."""
    with pytest.raises(ValidationError):
        Money(amount="1.00", currency="USDX")
    with pytest.raises(ValidationError):
        Money(amount="1.00", currency="DOLLARS")
    with pytest.raises(ValidationError):
        Money(amount="1.00", currency="usd")
    with pytest.raises(ValidationError):
        Money(amount="1.00", currency="US")


def test_money_accepts_string_and_decimal_and_int() -> None:
    assert Money(amount="186.00", currency="USD").amount == Decimal("186.00")
    assert Money(amount=Decimal("186.00"), currency="USD").amount == Decimal("186.00")
    assert Money(amount=186, currency="USD").amount == Decimal("186")


def test_money_json_wire_form_is_a_string() -> None:
    parsed = Money.model_validate_json('{"amount": "186.00", "currency": "USD"}')
    assert parsed.amount == Decimal("186.00")
    assert parsed.model_dump(mode="json") == {"amount": "186.00", "currency": "USD"}


def test_money_json_schema_declares_a_string_amount() -> None:
    """What constrains the Tier E structured-output call and the OpenAPI schema."""
    schema = Money.model_json_schema()
    assert schema["properties"]["amount"]["type"] == "string"


def test_money_arithmetic_refuses_cross_currency() -> None:
    usd = Money(amount="420.00", currency="USD")
    eur = Money(amount="200.00", currency="EUR")
    assert (usd - Money(amount="200.00", currency="USD")).amount == Decimal("220.00")
    assert (usd + Money(amount="1.50", currency="USD")).amount == Decimal("421.50")
    with pytest.raises(ValueError, match="currency mismatch"):
        _ = usd - eur


def test_money_is_frozen() -> None:
    money = Money(amount="1.00", currency="USD")
    with pytest.raises(ValidationError):
        money.amount = Decimal("2.00")


# ---------------------------------------------------------------------------
# L4 — timestamps are timezone-aware UTC
# ---------------------------------------------------------------------------


def test_utc_datetime_rejects_a_naive_value() -> None:
    """G1.5 rejection 4. The UI localises; the wire never does."""
    with pytest.raises(ValidationError) as excinfo:
        _Probe(at=datetime(2026, 6, 5, 12, 0, 0), score="0.5")
    assert "naive datetime rejected" in str(excinfo.value)


def test_utc_datetime_rejects_a_non_iso_string() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _Probe(at="the fifth of June", score="0.5")
    assert "not an ISO-8601 timestamp" in str(excinfo.value)


def test_offset_datetimes_are_normalised_to_utc() -> None:
    probe = _Probe(at="2026-06-05T14:00:00+02:00", score="0.5")
    assert probe.at == datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    assert probe.at.utcoffset() == timedelta(0)


def test_z_suffix_is_accepted() -> None:
    assert _Probe(at="2026-06-05T12:00:00Z", score="0").at.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# L3 — confidences and weights live in [0, 1]
# ---------------------------------------------------------------------------


def test_confidence_rejects_a_negative_value() -> None:
    """G1.5 rejection 1."""
    with pytest.raises(ValidationError):
        _Probe(at=JUNE_5, score="-0.01")


def test_confidence_rejects_a_value_above_one() -> None:
    """G1.5 rejection 2."""
    with pytest.raises(ValidationError):
        _Probe(at=JUNE_5, score="1.01")


def test_confidence_rejects_a_bool() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _Probe(at=JUNE_5, score=True)
    assert "bool is not a confidence" in str(excinfo.value)


def test_confidence_rejects_a_non_finite_value() -> None:
    with pytest.raises(ValidationError):
        _Probe(at=JUNE_5, score="NaN")
    with pytest.raises(ValidationError):
        _Probe(at=JUNE_5, score="Infinity")


def test_confidence_is_quantised_to_four_places() -> None:
    """Two runs emitting 0.8700000001 and 0.87 must hash identically."""
    assert _Probe(at=JUNE_5, score=0.876543).score == Decimal("0.8765")
    assert _Probe(at=JUNE_5, score="0.8700000001").score == Decimal("0.8700")
    assert _Probe(at=JUNE_5, score=0).score == Decimal("0.0000")
    assert _Probe(at=JUNE_5, score=1).score == Decimal("1.0000")


def test_weight_is_the_same_domain_as_confidence() -> None:
    assert base.Weight is base.Confidence


# ---------------------------------------------------------------------------
# Half-open validity intervals — [valid_from, valid_to), valid_to = NULL open
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "build"), INTERVAL_BUILDERS, ids=[n for n, _ in INTERVAL_BUILDERS]
)
def test_half_open_interval_rejects_valid_to_at_or_before_valid_from(name: str, build: Any) -> None:
    """G1.5 rejection 5, on every contract in these modules that carries the pair.

    ``valid_to == valid_from`` is rejected as well as ``valid_to < valid_from``:
    the interval is half-open, so an equal pair describes zero elapsed validity,
    which is never what an extractor meant.
    """
    with pytest.raises(ValidationError) as excinfo:
        build(valid_from=JULY_1, valid_to=JUNE_1)
    assert "half-open" in str(excinfo.value), name

    with pytest.raises(ValidationError):
        build(valid_from=JUNE_1, valid_to=JUNE_1)


@pytest.mark.parametrize(
    ("name", "build"), INTERVAL_BUILDERS, ids=[n for n, _ in INTERVAL_BUILDERS]
)
def test_half_open_interval_accepts_an_open_ended_upper_bound(name: str, build: Any) -> None:
    """``valid_to = NULL`` means "still true as far as we know"."""
    still_true = build(valid_from=JUNE_1, valid_to=None)
    assert still_true.valid_to is None, name
    ordered = build(valid_from=JUNE_1, valid_to=JULY_1)
    assert ordered.valid_from < ordered.valid_to


def test_half_open_interval_membership_excludes_the_upper_bound() -> None:
    interval = HalfOpenInterval(valid_from=JUNE_1, valid_to=JULY_1)
    assert interval.contains(JUNE_1) is True
    assert interval.contains(JUNE_5) is True
    assert interval.contains(JULY_1) is False
    assert interval.is_open_ended is False
    assert HalfOpenInterval(valid_from=JUNE_1).is_open_ended is True
    assert HalfOpenInterval(valid_from=JUNE_1).contains(JULY_1) is True


# ---------------------------------------------------------------------------
# L1 — schema_version on every boundary contract
# ---------------------------------------------------------------------------


def test_boundary_contract_rejects_a_missing_schema_version() -> None:
    """G1.5 rejection 7. See the module docstring for what "missing" means here."""
    for absent in (None, "", "1", "v1", "1.0.0", "  "):
        with pytest.raises(ValidationError):
            _BoundaryProbe.model_validate({"value": "x", "schema_version": absent})


def test_boundary_contract_rejects_an_unsupported_schema_major() -> None:
    assert frozenset({"1"}) == SUPPORTED_SCHEMA_MAJORS
    with pytest.raises(ValidationError) as excinfo:
        _BoundaryProbe.model_validate({"value": "x", "schema_version": "2.0"})
    assert "unsupported schema major" in str(excinfo.value)


def test_boundary_contract_defaults_and_round_trips_schema_version() -> None:
    assert _BoundaryProbe().schema_version == SCHEMA_VERSION
    assert _BoundaryProbe(schema_version="1.4").schema_version == "1.4"
    assert json.loads(_BoundaryProbe().model_dump_json())["schema_version"] == SCHEMA_VERSION


def test_every_boundary_contract_in_these_modules_carries_schema_version() -> None:
    """T1.5 acceptance: every model in the six modules carries ``schema_version``.

    Recorded deviation, reported rather than resolved: ``specs/11_CONTRACTS.md``
    section 6 splits the base into ``Contract`` (no ``schema_version``) and
    ``BoundaryContract`` (``schema_version``), and section 20.9's lint —
    ``schema-version-present``, landing in T1.6 — is written against *boundary*
    models. Putting the field on ``Contract`` would break section 20.3's own
    assertion that ``Money.model_dump(mode="json")`` is exactly
    ``{"amount": ..., "currency": ...}``. The spec outranks the task plan, so
    the census below asserts the rule against every contract that actually
    crosses a boundary, and :func:`test_value_objects_are_the_documented_carve_out`
    pins the exceptions so a new one cannot be added silently.
    """
    boundary = _contracts_in_modules(BoundaryContract)
    assert boundary, "no boundary contracts found — the modules did not import"
    for name, model in sorted(boundary.items()):
        assert "schema_version" in model.model_fields, name
        assert model.model_fields["schema_version"].default == SCHEMA_VERSION, name


def test_value_objects_are_the_documented_carve_out() -> None:
    """The non-boundary contracts, enumerated. A new one is a deliberate choice."""
    non_boundary = {
        name
        for name, model in _contracts_in_modules(Contract).items()
        if not issubclass(model, BoundaryContract)
    }
    assert non_boundary == {
        # base
        "Money",
        "HalfOpenInterval",
        # identity
        "CapabilityBinding",
        # ingestion
        "ContentLocator",
        "SourceLocator",
        "ContentBlock",
        "CounterpartyHint",
        "ExternalIdentifier",
        "DateMention",
        "AmountMention",
        "EvidenceCandidate",
        "ClaimCandidate",
        "CommitmentCandidate",
        "ProspectiveCue",
        "InjectionObservation",
        "Uncertainty",
        # retrieval
        "MatchSignal",
        "EvidenceSnippet",
        "CanonicalBeliefSummary",
        "ActiveConflictSummary",
        "ActiveCommitmentSummary",
        "TemporalFact",
        "McpToolCall",
        "VectorSearchParams",
        "RetrievalDebug",
        # resolution
        "ResolvedIdentity",
        "SemanticRelationAssertion",
        "TemporalInterpretation",
        "ProposedSupersession",
        # predicates
        "PredicateNode",
    }


def _contracts_in_modules(kind: type[Contract]) -> dict[str, type[Contract]]:
    found: dict[str, type[Contract]] = {}
    for module in SIX_MODULES:
        for name, value in vars(module).items():
            if name.startswith("_") or not isinstance(value, type):
                continue
            if not issubclass(value, kind) or value in (Contract, BoundaryContract):
                continue
            if value.__module__ != module.__name__:
                continue  # re-export, counted where it is defined
            found[name] = value
    return found


# ---------------------------------------------------------------------------
# L9 — immutability and the closed field set
# ---------------------------------------------------------------------------


def test_contract_rejects_an_extra_field() -> None:
    """An agent cannot smuggle an authority score into a contract."""
    with pytest.raises(ValidationError) as excinfo:
        Money.model_validate({"amount": "1.00", "currency": "USD", "authority_score": "1.0"})
    assert "authority_score" in str(excinfo.value)


def test_boundary_contract_rejects_an_extra_field() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _BoundaryProbe.model_validate({"value": "x", "_bypass_review": True})
    assert "_bypass_review" in str(excinfo.value)


def test_contracts_are_frozen() -> None:
    probe = _BoundaryProbe()
    with pytest.raises(ValidationError):
        probe.value = "y"


# ---------------------------------------------------------------------------
# L5 — identifier types that structurally cannot carry SQL
# ---------------------------------------------------------------------------


class _IdProbe(Contract):
    predicate: base.SafeIdentifier
    code: base.ReasonCode
    local_id: base.LocalId
    block_id: base.BlockId
    digest: base.Sha256Hex
    key: base.IdempotencyKey
    revision: base.Revision


def _id_probe(**overrides: Any) -> _IdProbe:
    payload: dict[str, Any] = {
        "predicate": "commitment.deposit.outstanding_amount",
        "code": "CONTRADICTORY_EVIDENCE",
        "local_id": "ev_1",
        "block_id": "blk_body1",
        "digest": SHA,
        "key": "ingest-2026-06-05-0001",
        "revision": 7,
    }
    payload.update(overrides)
    return _IdProbe(**payload)


def test_safe_identifier_rejects_sql_shaped_text() -> None:
    assert _id_probe().predicate == "commitment.deposit.outstanding_amount"
    for hostile in (
        "DROP TABLE cases",
        "commitment.amount; DELETE FROM evidence_items",
        "commitment'--",
        "commitment.amount) OR (1=1",
        "Commitment.Amount",
    ):
        with pytest.raises(ValidationError):
            _id_probe(predicate=hostile)


def test_identifier_types_reject_the_wrong_shape() -> None:
    with pytest.raises(ValidationError):
        _id_probe(code="lower_case_reason")
    with pytest.raises(ValidationError):
        _id_probe(local_id="xx_1")  # unknown kind prefix
    with pytest.raises(ValidationError):
        _id_probe(block_id="body1")  # no blk_ prefix
    with pytest.raises(ValidationError):
        _id_probe(digest="NOTHEX")
    with pytest.raises(ValidationError):
        _id_probe(key="short")  # under 8 characters
    with pytest.raises(ValidationError):
        _id_probe(revision=-1)


def test_sha256_hex_rejects_upper_case() -> None:
    """Pins a measured pydantic ordering, and a spec flag that does nothing.

    ``specs/11_CONTRACTS.md`` section 6 declares
    ``Sha256Hex = Annotated[str, StringConstraints(pattern=..., to_lower=True)]``,
    which reads as "normalise the case". Measured on the pinned
    ``pydantic 2.13.4``: ``StringConstraints`` applies ``to_lower`` **after**
    ``pattern``, so an upper-case digest is refused by the pattern before it can
    be normalised and the flag is inert. Refusing is the safe direction —
    ``hashlib.sha256().hexdigest()`` is lower case, so nothing this system
    produces is affected — and the flag is left exactly as the spec declares it
    rather than being quietly made effective. Reported, not resolved.
    """
    assert _id_probe(digest=SHA).digest == SHA
    with pytest.raises(ValidationError):
        _id_probe(digest=SHA.upper())


# ---------------------------------------------------------------------------
# Canonical serialisation, hashing, identifiers
# ---------------------------------------------------------------------------


def test_canonical_json_is_sorted_compact_and_omits_none() -> None:
    payload = canonical_json(Money(amount="186.00", currency="USD"))
    assert payload == b'{"amount":"186.00","currency":"USD"}'
    locator = _text_span()
    text = canonical_json(locator).decode()
    assert '"mime_part"' not in text  # None omitted
    assert text.index('"block_id"') < text.index('"char_end"') < text.index('"kind"')


def test_content_hash_is_stable_across_field_order() -> None:
    first = Money(amount="186.00", currency="USD")
    second = Money.model_validate({"currency": "USD", "amount": "186.00"})
    assert content_hash(first) == content_hash(second)
    assert len(content_hash(first)) == 64
    assert content_hash(first) != content_hash(Money(amount="186.01", currency="USD"))


def test_content_hash_can_exclude_fields() -> None:
    probe = _BoundaryProbe()
    assert content_hash(probe) != content_hash(probe, exclude=frozenset({"schema_version"}))


def test_new_id_is_a_unique_uuid_and_names_its_generator() -> None:
    first, second = new_id(), new_id()
    assert isinstance(first, uuid.UUID)
    assert first != second
    assert isinstance(base.UUID_GENERATOR, str)
    assert base.UUID_GENERATOR


def test_utc_now_is_timezone_aware() -> None:
    """The only clock read in the contracts package, and it is aware."""
    now = base.utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# identity.py — contract law L10 / 15_API_SPEC.md section 3
# ---------------------------------------------------------------------------


def _principal(**overrides: Any) -> Principal:
    payload: dict[str, Any] = {
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "cognito_sub": "9c1f0a52-0000-4000-8000-000000000001",
        "token_issued_at": JUNE_5,
        "token_expires_at": JUNE_5 + timedelta(hours=1),
        "request_id": uuid.uuid4(),
        "trace_id": uuid.uuid4(),
    }
    payload.update(overrides)
    return Principal(**payload)


def test_internal_principal_rejects_a_caller_supplied_user_id() -> None:
    """``specs/15_API_SPEC.md`` section 3.2, encoded in the type.

    A machine client never asserts its own ``user_id``. The rule is not a route
    check that can be forgotten on the next endpoint: ``InternalPrincipal`` has
    no such field, and ``extra="forbid"`` means one cannot be added at runtime.
    """
    assert "user_id" not in InternalPrincipal.model_fields
    assert "tenant_id" not in InternalPrincipal.model_fields
    with pytest.raises(ValidationError) as excinfo:
        InternalPrincipal.model_validate(
            {
                "app_client": "provenance-agent-runtime",
                "workload": "AGENT_RUNTIME",
                "scopes": ["provenance.memory/read"],
                "token_issued_at": JUNE_5,
                "token_expires_at": JUNE_5 + timedelta(minutes=15),
                "request_id": str(uuid.uuid4()),
                "trace_id": str(uuid.uuid4()),
                "user_id": str(uuid.uuid4()),
            }
        )
    assert "user_id" in str(excinfo.value)


def test_principal_rejects_an_inverted_token_window() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _principal(token_expires_at=JUNE_5 - timedelta(seconds=1))
    assert "token_expires_at must be after token_issued_at" in str(excinfo.value)


# ---------------------------------------------------------------------------
# ingestion.py — quoted history is a tag, not a heuristic
# ---------------------------------------------------------------------------


def _content_block(**overrides: Any) -> ContentBlock:
    payload: dict[str, Any] = {
        "block_id": "blk_body1",
        "artifact_id": uuid.uuid4(),
        "ordinal": 0,
        "kind": ContentBlockKind.BODY,
        "text": "We will return the deposit within 30 days of inspection.",
        "content_sha256": SHA,
        "source_locator": _text_span(),
    }
    payload.update(overrides)
    return ContentBlock(**payload)


def test_content_block_preserves_quoted_history_tagging() -> None:
    """A promise found only inside quoted history is not a new promise."""
    assert _content_block().is_quoted_history is False
    quoted = _content_block(kind=ContentBlockKind.QUOTED_HISTORY)
    assert quoted.is_quoted_history is True
    assert quoted.kind is ContentBlockKind.QUOTED_HISTORY


def test_content_block_is_always_untrusted() -> None:
    assert _content_block().trust_class == "UNTRUSTED"
    with pytest.raises(ValidationError):
        _content_block(trust_class="TRUSTED_CANONICAL")


def test_content_block_rejects_a_locator_pointing_at_another_block() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _content_block(source_locator=_text_span("blk_body2"))
    assert "does not match block_id" in str(excinfo.value)


def test_evidence_candidate_rejects_a_hallucinated_locator() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _evidence_candidate(source_locator=_text_span("blk_other"))
    assert "source_locator must point at the cited block" in str(excinfo.value)


def test_evidence_candidate_rejects_the_wrong_local_id_prefix() -> None:
    with pytest.raises(ValidationError):
        _evidence_candidate(local_id="cl_1")


# ---------------------------------------------------------------------------
# predicates.py — the safe trigger AST
# ---------------------------------------------------------------------------


def _field(path: str) -> PredicateNode:
    return PredicateNode(op=PredicateOp.FIELD, path=path)


def test_predicate_ast_rejects_arithmetic_ops() -> None:
    """``CANONICAL_DECISIONS.md`` -> "Trigger arithmetic", as a closed grammar.

    There is no ADD, SUB, MUL or DIV member to build a node from, so a derived
    comparison must go through a reviewed named projection field
    (``commitments.deposit.outstanding_amount``) rather than being computed
    inside the predicate. This test fails the moment an arithmetic member is
    added to ``PredicateOp`` without a corresponding decision.
    """
    for arithmetic in ("ADD", "SUB", "MUL", "DIV", "MOD", "POW", "CONCAT", "CALL"):
        assert not hasattr(PredicateOp, arithmetic)
    with pytest.raises(ValidationError):
        PredicateNode.model_validate({"op": "ADD", "args": []})
    assert set(predicates.PREDICATE_ARITY) == set(PredicateOp)


def test_predicate_rejects_a_field_root_outside_the_registry() -> None:
    with pytest.raises(ValidationError):
        _field("evidence_items.normalized_text")
    with pytest.raises(ValidationError):
        _field("case")  # a root alone is not a projection field
    assert _field("commitments.deposit.outstanding_amount").path


def test_predicate_rejects_a_node_that_is_both_leaf_and_branch() -> None:
    with pytest.raises(ValidationError) as excinfo:
        PredicateNode(op=PredicateOp.FIELD, path="clock.now", value="0")
    assert "FIELD must not carry a value" in str(excinfo.value)
    with pytest.raises(ValidationError):
        PredicateNode(op=PredicateOp.CONST, value="0", path="clock.now")
    with pytest.raises(ValidationError):
        PredicateNode(op=PredicateOp.AND, path="clock.now")


def test_predicate_rejects_the_wrong_arity() -> None:
    with pytest.raises(ValidationError):
        PredicateNode(op=PredicateOp.NOT, args=(_field("clock.now"), _field("case.status")))
    with pytest.raises(ValidationError):
        PredicateNode(op=PredicateOp.AND, args=(_field("clock.now"),))
    with pytest.raises(ValidationError):
        PredicateNode(op=PredicateOp.FIELD, path="clock.now", args=(_field("case.status"),))


def test_predicate_rejects_an_evaluator_bomb() -> None:
    node = _field("clock.now")
    with pytest.raises(ValidationError) as excinfo:
        for _ in range(MAX_PREDICATE_DEPTH + 2):
            node = PredicateNode(op=PredicateOp.NOT, args=(node,))
    assert f"exceeds {MAX_PREDICATE_DEPTH}" in str(excinfo.value)
    assert node.depth() == MAX_PREDICATE_DEPTH  # the last term that validated


def test_the_landlord_deposit_predicate_is_expressible() -> None:
    """The hero trigger, in the grammar of section 11."""
    deposit_overdue = PredicateNode(
        op=PredicateOp.AND,
        args=(
            PredicateNode(
                op=PredicateOp.GT,
                args=(
                    _field("commitments.deposit.outstanding_amount"),
                    PredicateNode(op=PredicateOp.CONST, value="0"),
                ),
            ),
            PredicateNode(
                op=PredicateOp.GTE,
                args=(_field("clock.now"), _field("commitments.deposit.due_at")),
            ),
        ),
    )
    assert deposit_overdue.depth() == 3
    assert deposit_overdue.field_paths() == frozenset(
        {
            "commitments.deposit.outstanding_amount",
            "clock.now",
            "commitments.deposit.due_at",
        }
    )
