"""The registry: five views, their projections, and the filters a caller may bind.

Authority
---------
- ``docs/CANONICAL_DECISIONS.md`` -> *Names and counts*: "Agent-safe views:
  ``agent_case_context_v1``, ``agent_active_beliefs_v1``,
  ``agent_belief_lineage_v1``, ``agent_evidence_retrieval_v1``,
  ``agent_open_obligations_v1``." Named here through
  :class:`provenance_domain.enums.AgentSafeView` so there is one spelling in the
  tree rather than a copy that can drift.
- ``db/migrations/versions/0008_events_infrastructure.py`` -> ``VIEW_DDL``. Every
  column tuple below is transcribed from the deployed ``CREATE VIEW``, in the
  order the view projects it, and
  ``services/control_plane/tests/db/test_mcp_server.py`` re-reads both against
  the live cluster. A registry transcribed from memory produces a ``42703``
  ``UndefinedColumn`` at the worst possible moment; this one is checked against
  ``information_schema`` on every db run.
- ``docs/implementation/00_IMPLEMENTATION_MAP.md`` section 12 - no arbitrary SQL
  tool is exposed to agents.

Why the registry is the allowlist
---------------------------------
A tool's parameters are not a query language. Each :class:`FilterSpec` names one
view column, one value kind and, where the column carries a closed vocabulary,
the exact membership. Everything a caller supplies is either bound as a value
against one of these or refused; nothing a caller supplies is ever concatenated
into a statement. The two consequences worth stating are that the statement text
for a given tool depends only on *which* filters were named and never on their
values, and that a filter naming a column the view does not project cannot be
declared without failing
``tests/mcp/test_tool_surface.py::test_every_declared_filter_binds_a_column_the_view_projects``.

What is deliberately absent
---------------------------
``tenant_id`` and ``user_id`` are projected by every view - they have to be, or
the scoping predicate would be unwritable - and neither is a declarable filter.
They are bound from :class:`~services.control_plane.app.mcp.scope.AgentScope`,
which comes from the caller's verified identity. There is no tool parameter for
either, and :mod:`services.control_plane.app.mcp.statements` refuses one even if
a later edit declared it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from provenance_domain.enums import (
    AgentSafeView,
    AttentionLevel,
    CaseStatus,
    CaseType,
    EpistemicStatus,
    EvidenceType,
)

__all__ = [
    "AGENT_VIEW_TOOLS",
    "AgentViewTool",
    "DEFAULT_ROWS",
    "FilterKind",
    "FilterSpec",
    "MAX_ROWS",
    "SCOPE_COLUMNS",
]

#: The two columns every statement scopes by, bound from the verified identity.
#: Named as a constant so the statement builder and the tests that police it read
#: the same tuple.
SCOPE_COLUMNS: Final[tuple[str, str]] = ("tenant_id", "user_id")

#: The page an agent gets when it does not ask for one, and the ceiling it
#: cannot raise. An unbounded page is a denial-of-service tool with a friendly
#: name, and a bound the caller can choose is not a bound.
DEFAULT_ROWS: Final[int] = 25
MAX_ROWS: Final[int] = 200

#: The two row kinds ``agent_open_obligations_v1`` emits. They are literals in
#: the view's own ``UNION ALL`` (migration ``0008``), not members of any domain
#: enum, so they are transcribed here with that citation rather than invented.
OBLIGATION_ROW_KINDS: Final[frozenset[str]] = frozenset({"COMMITMENT", "CONFLICT"})

#: The statuses that view can emit: its ``WHERE`` admits four commitment
#: statuses and two conflict statuses and nothing else. Allowing the full
#: product of both enums would advertise filters that can only ever return zero
#: rows.
OBLIGATION_STATUSES: Final[frozenset[str]] = frozenset(
    {"PROPOSED", "ACTIVE", "PARTIAL", "DISPUTED", "OPEN", "NEEDS_HUMAN"}
)


class FilterKind(StrEnum):
    """How a supplied value is validated before it is bound.

    Validation is not decoration here: it is what turns "the value is bound"
    from a claim about this module into a claim about the value's *type*. A
    malformed UUID is refused rather than bound, so a caller cannot use a filter
    slot as a place to park text.
    """

    UUID = "UUID"
    TEXT = "TEXT"
    ENUM = "ENUM"


@dataclass(frozen=True, slots=True)
class FilterSpec:
    """One bindable predicate: ``<column> = %s``.

    ``name`` is the tool parameter a caller sees; ``column`` is the view column
    it binds. They are usually equal and are kept separate so a parameter can be
    renamed for the model without moving the SQL.
    """

    name: str
    column: str
    kind: FilterKind
    description: str
    allowed: frozenset[str] = frozenset()
    max_length: int = 200
    required: bool = False


@dataclass(frozen=True, slots=True)
class AgentViewTool:
    """One tool, one view, one fixed statement shape."""

    tool_name: str
    view: AgentSafeView
    description: str
    columns: tuple[str, ...]
    filters: tuple[FilterSpec, ...]
    order_by: tuple[str, ...]
    default_limit: int = DEFAULT_ROWS
    max_limit: int = MAX_ROWS

    @property
    def view_name(self) -> str:
        """The relation this tool reads. The canon spelling, from the enum."""
        return str(self.view.value)

    def filter_by_name(self, name: str) -> FilterSpec | None:
        for spec in self.filters:
            if spec.name == name:
                return spec
        return None


def _enum_values(*members: object) -> frozenset[str]:
    return frozenset(str(member) for member in members)


# ---------------------------------------------------------------------------
# V1 - agent_case_context_v1
# ---------------------------------------------------------------------------

_CASE_CONTEXT = AgentViewTool(
    tool_name="read_case_context",
    view=AgentSafeView.CASE_CONTEXT,
    description=(
        "Read the caller's open cases with their counterparty and relationship "
        "context. Scoped to the caller; no owner may be named."
    ),
    columns=(
        "tenant_id",
        "user_id",
        "case_id",
        "title",
        "case_type",
        "status",
        "revision",
        "attention_level",
        "opened_at",
        "resolved_at",
        "last_activity_at",
        "reopened_count",
        "relationship_id",
        "relationship_type",
        "external_account_ref",
        "counterparty_name",
        "counterparty_kind",
        "counterparty_domain",
        "context_title",
    ),
    filters=(
        FilterSpec(
            name="case_id",
            column="case_id",
            kind=FilterKind.UUID,
            description="Restrict to one case.",
        ),
        FilterSpec(
            name="status",
            column="status",
            kind=FilterKind.ENUM,
            description="Case lifecycle status.",
            allowed=_enum_values(*CaseStatus),
        ),
        FilterSpec(
            name="case_type",
            column="case_type",
            kind=FilterKind.ENUM,
            description="Case type.",
            allowed=_enum_values(*CaseType),
        ),
        FilterSpec(
            name="attention_level",
            column="attention_level",
            kind=FilterKind.ENUM,
            description="Attention level: NONE, INFO, ATTENTION or URGENT.",
            allowed=_enum_values(*AttentionLevel),
        ),
        FilterSpec(
            name="counterparty_name",
            column="counterparty_name",
            kind=FilterKind.TEXT,
            description="Exact counterparty display name.",
            max_length=200,
        ),
    ),
    order_by=("last_activity_at DESC", "case_id ASC"),
)


# ---------------------------------------------------------------------------
# V2 - agent_active_beliefs_v1
# ---------------------------------------------------------------------------

_ACTIVE_BELIEFS = AgentViewTool(
    tool_name="read_active_beliefs",
    view=AgentSafeView.ACTIVE_BELIEFS,
    description=(
        "Read the caller's current belief versions with their grounding edges. "
        "Retracted versions are excluded inside the view."
    ),
    columns=(
        "tenant_id",
        "user_id",
        "case_id",
        "belief_id",
        "subject_type",
        "subject_id",
        "predicate",
        "belief_version_id",
        "version_no",
        "value_type",
        "value_json",
        "epistemic_status",
        "belief_confidence",
        "derivation_kind",
        "valid_from",
        "valid_to",
        "recorded_at",
        "grounding_relation",
        "grounding_source_kind",
        "grounding_source_id",
        "grounding_weight",
        "grounding_reason_code",
    ),
    filters=(
        FilterSpec(
            name="case_id",
            column="case_id",
            kind=FilterKind.UUID,
            description="Restrict to one case.",
        ),
        FilterSpec(
            name="belief_id",
            column="belief_id",
            kind=FilterKind.UUID,
            description="Restrict to one belief.",
        ),
        FilterSpec(
            name="epistemic_status",
            column="epistemic_status",
            kind=FilterKind.ENUM,
            description="Epistemic status of the current version.",
            allowed=_enum_values(*EpistemicStatus),
        ),
        FilterSpec(
            name="predicate",
            column="predicate",
            kind=FilterKind.TEXT,
            description="Exact belief predicate.",
            max_length=200,
        ),
    ),
    order_by=("recorded_at DESC", "belief_version_id ASC"),
)


# ---------------------------------------------------------------------------
# V3 - agent_belief_lineage_v1
# ---------------------------------------------------------------------------

_BELIEF_LINEAGE = AgentViewTool(
    tool_name="read_belief_lineage",
    view=AgentSafeView.BELIEF_LINEAGE,
    description=(
        "Read the ordered supersession chain for one belief, with the kernel "
        "decision and reason codes behind each change."
    ),
    columns=(
        "tenant_id",
        "user_id",
        "belief_id",
        "belief_version_id",
        "version_no",
        "value_json",
        "epistemic_status",
        "recorded_at",
        "superseded_at",
        "supersedes_version_id",
        "supersession_reason_code",
        "kernel_decision",
        "kernel_reason_codes",
        "trace_id",
    ),
    filters=(
        # Required, and deliberately: lineage is a chain, and a chain query with
        # no anchor is a table scan wearing a tool's name.
        FilterSpec(
            name="belief_id",
            column="belief_id",
            kind=FilterKind.UUID,
            description="The belief whose lineage to read.",
            required=True,
        ),
        FilterSpec(
            name="epistemic_status",
            column="epistemic_status",
            kind=FilterKind.ENUM,
            description="Restrict to versions in one epistemic status.",
            allowed=_enum_values(*EpistemicStatus),
        ),
        FilterSpec(
            name="supersession_reason_code",
            column="supersession_reason_code",
            kind=FilterKind.TEXT,
            description="Exact supersession reason code.",
            max_length=64,
        ),
    ),
    order_by=("version_no ASC",),
)


# ---------------------------------------------------------------------------
# V4 - agent_evidence_retrieval_v1
# ---------------------------------------------------------------------------
#
# The view applies ``retraction_status = 'ACTIVE'`` itself, so there is no
# retraction filter to declare and nothing for a caller to forget. It also
# withholds ``exact_text``, ``source_locator`` and ``embedding``; the projection
# below is the whole reachable surface, and there is no ``case_id`` column on
# this view - a filter for one would be a ``42703``, which is exactly the kind
# of thing a registry written from memory invents.

_EVIDENCE_RETRIEVAL = AgentViewTool(
    tool_name="read_evidence",
    view=AgentSafeView.EVIDENCE_RETRIEVAL,
    description=(
        "Read the caller's retrieval-eligible evidence with its source artifact "
        "metadata. Retracted and superseded evidence is excluded inside the view."
    ),
    columns=(
        "tenant_id",
        "user_id",
        "evidence_id",
        "artifact_id",
        "evidence_type",
        "normalized_text",
        "actor_ref",
        "valid_from",
        "valid_to",
        "observed_at",
        "extraction_confidence",
        "source_authority",
        "embedding_version",
        "source_type",
        "sender_domain",
        "artifact_subject",
        "artifact_received_at",
    ),
    filters=(
        FilterSpec(
            name="evidence_id",
            column="evidence_id",
            kind=FilterKind.UUID,
            description="Restrict to one evidence item.",
        ),
        FilterSpec(
            name="artifact_id",
            column="artifact_id",
            kind=FilterKind.UUID,
            description="Restrict to the evidence extracted from one artifact.",
        ),
        FilterSpec(
            name="evidence_type",
            column="evidence_type",
            kind=FilterKind.ENUM,
            description="Evidence assertion type.",
            allowed=_enum_values(*EvidenceType),
        ),
        FilterSpec(
            name="sender_domain",
            column="sender_domain",
            kind=FilterKind.TEXT,
            description="Exact sending domain of the source artifact.",
            max_length=253,
        ),
    ),
    order_by=("observed_at DESC", "evidence_id ASC"),
)


# ---------------------------------------------------------------------------
# V5 - agent_open_obligations_v1
# ---------------------------------------------------------------------------

_OPEN_OBLIGATIONS = AgentViewTool(
    tool_name="read_open_obligations",
    view=AgentSafeView.OPEN_OBLIGATIONS,
    description=(
        "Read the caller's unresolved obligations: open commitments and open "
        "conflicts, in one ordered list."
    ),
    columns=(
        "tenant_id",
        "user_id",
        "case_id",
        "row_kind",
        "row_id",
        "subtype",
        "status",
        "summary",
        "currency",
        "committed_amount",
        "fulfilled_amount",
        "outstanding_amount",
        "due_at",
        "severity",
    ),
    filters=(
        FilterSpec(
            name="case_id",
            column="case_id",
            kind=FilterKind.UUID,
            description="Restrict to one case.",
        ),
        FilterSpec(
            name="row_kind",
            column="row_kind",
            kind=FilterKind.ENUM,
            description="COMMITMENT or CONFLICT.",
            allowed=OBLIGATION_ROW_KINDS,
        ),
        FilterSpec(
            name="status",
            column="status",
            kind=FilterKind.ENUM,
            description="Commitment or conflict status.",
            allowed=OBLIGATION_STATUSES,
        ),
        FilterSpec(
            name="subtype",
            column="subtype",
            kind=FilterKind.TEXT,
            description="Exact commitment type or conflict type.",
            max_length=64,
        ),
    ),
    order_by=("due_at ASC", "row_id ASC"),
)


#: The whole exposed surface, keyed by tool name. Five entries, one per view.
#: Adding a sixth entry that is not an ``AgentSafeView`` member fails
#: ``tests/mcp/test_tool_surface.py``; adding a sixth view to the database
#: without adding it here leaves it unreachable, which is the safe direction.
AGENT_VIEW_TOOLS: Final[dict[str, AgentViewTool]] = {
    tool.tool_name: tool
    for tool in (
        _CASE_CONTEXT,
        _ACTIVE_BELIEFS,
        _BELIEF_LINEAGE,
        _EVIDENCE_RETRIEVAL,
        _OPEN_OBLIGATIONS,
    )
}
