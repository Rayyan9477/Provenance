"""The 18,000-row synthetic decoy corpus (``T2.8`` steps 5 and 6).

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` section 17.7.
- ``docs/quality/22_EVAL_DATASETS.md`` section 7 -- the eight mandatory
  generation rules, the plan, and ``NEAR_MISS_QUOTA``.
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 12.1 -- templates render into the
  **embedding text template** only, and never contain an identifier.

The separation this file exists to hold
---------------------------------------
> Canonical business state stays small and hand-curated. The vector index gets
> large and synthetic. The two never mix in the UI.

Vector retrieval over 32 hand-curated rows is a lookup with extra steps, and a
judge is right to be unimpressed by a top-1 hit in a corpus of thirty. But
18,000 hand-curated business facts would make canonical state unexplainable and
the dashboard would stop being a product. Decoys carry
``source_type = 'SEED_FIXTURE'`` so every UI query excludes them, and they
inflate the index and never the dashboard.

The 120 near-misses are the point
---------------------------------
They are ISP invoices from *other* providers, for *other* billing periods, at
amounts within USD 25 of the hero invoice's 186.00. Without them the retrieval
eval measures recall against noise; with them it measures discrimination.
``NEAR_MISS_QUOTA`` is the parameter that decides whether ``22_EVAL_DATASETS.md``
section 5.3's identity gates mean anything.

Uniqueness, and why it is engineered rather than hoped for
----------------------------------------------------------
``idx_evidence_text_hash`` and the embedding cache are both keyed on text, and
two decoys with identical text would share one vector -- which
``22_EVAL_DATASETS.md`` section 7.2 rule 4 forbids ("no reused vectors"). With
18,000 rows drawn from eight templates and forty counterparties, collisions are
not unlikely, they are certain. Every decoy therefore carries a correspondence
reference built from a **word triple indexed by its position**, so uniqueness is
a property of the construction rather than of the random draw. The words are
lower-case English, so the reference cannot be mistaken for an identifier by
``13_RETRIEVAL_SPEC.md`` section 12.1 rule 5 -- or by a reader.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Any
from uuid import UUID

from scripts.seed.artifacts import S3_BUCKET
from scripts.seed.embedding_text import build_embedding_text
from scripts.seed.ids import DEMO_ANCHOR_UTC, LOOKBACK_DAYS, sid
from scripts.seed.rows import SeedArtifact, SeedEvidence
from scripts.seed.tenants import USERS, user_of

__all__ = [
    "DECOY_PLAN",
    "Decoy",
    "NEAR_MISS_QUOTA",
    "RNG_SEED",
    "corpus_fingerprint",
    "generate_decoys",
]

#: ``10_DATABASE_DDL.md`` section 17.7 and ``22_EVAL_DATASETS.md`` section 7.2.
DECOY_PLAN: dict[str, int] = {"hero": 16_000, "iso-a": 1_000, "iso-b": 1_000}

#: Rows engineered to sit close to the June invoice in vector space.
NEAR_MISS_QUOTA = 120

#: ``random.Random(20260817)`` -- so the corpus is byte-identical across
#: machines and an eval number is comparable between them.
RNG_SEED = 20260817

_HERO_INVOICE_AMOUNT = Decimal("186.00")
_NEAR_MISS_WINDOW = Decimal("25.00")

# ---------------------------------------------------------------------------
# The counterparty pool -- roughly forty fictional names
# ---------------------------------------------------------------------------

_ISP_POOL: tuple[str, ...] = (
    "Meridian Broadband",
    "Sable Point Networks",
    "Fernwood Telecom",
    "Aster Line Internet",
    "Copperfield Connect",
    "Halcyon Wireless",
    "Trillium Fiber Group",
    "Rookery Data Services",
    "Quarry Hill Comms",
    "Lantern Bay Broadband",
)

_UTILITY_POOL: tuple[str, ...] = (
    "Ironbridge Electric",
    "Wren Valley Gas",
    "Selkirk Water Authority",
    "Pinecrest Power",
    "Foxglove Utilities",
)

_LANDLORD_POOL: tuple[str, ...] = (
    "Ashgrove Residential",
    "Kingfisher Estates",
    "Marlowe Letting",
    "Stonecrop Property Group",
    "Winterbourne Rentals",
)

_RETAIL_POOL: tuple[str, ...] = (
    "Ledgerwood Supply",
    "Pale Fox Furnishings",
    "Bramble & Co",
    "Northgate Outfitters",
    "Verity Home Goods",
    "Alderman Hardware",
)

_EMPLOYER_POOL: tuple[str, ...] = (
    "Draycott Systems",
    "Lyre Analytics Group",
    "Ostrea Consulting",
    "Pemberton Labs",
    "Sixpenny Software",
)

_LOGISTICS_POOL: tuple[str, ...] = (
    "Cobble Lane Removals",
    "Tern Freight",
    "Hollow Oak Logistics",
    "Ridgeback Haulage",
    "Saltmarsh Delivery",
)

_SERVICES_POOL: tuple[str, ...] = (
    "Harrowgate Dental",
    "Cranmere Clinic",
    "Bellweather Insurance",
    "Thicket Lane Garage",
    "Old Mill Veterinary",
    "Cobalt Legal",
)

_ALL_POOLS: dict[str, tuple[str, ...]] = {
    "ISP": _ISP_POOL,
    "UTILITY": _UTILITY_POOL,
    "LANDLORD": _LANDLORD_POOL,
    "RETAIL": _RETAIL_POOL,
    "EMPLOYER": _EMPLOYER_POOL,
    "LOGISTICS": _LOGISTICS_POOL,
    "SERVICES": _SERVICES_POOL,
}

#: The isolation tenants reuse the hero's vocabulary deliberately
#: (``22_EVAL_DATASETS.md`` section 7.2 rule 3): same ISP name, same amounts,
#: same dates. If the ``user_id`` vector-index prefix or a tenant foreign key is
#: ever wrong, these rows leak and the isolation test fails loudly instead of
#: passing silently on an empty database.
_ISO_MIRROR_NAMES: tuple[str, ...] = (
    "Northline Fiber",
    "Harborview Property Management",
    "Beltline Movers",
)

# ---------------------------------------------------------------------------
# The word list behind the correspondence reference
# ---------------------------------------------------------------------------

_WORDS: tuple[str, ...] = (
    "amber",
    "anchor",
    "arbor",
    "aspen",
    "basin",
    "beacon",
    "birch",
    "bramble",
    "cedar",
    "cinder",
    "clover",
    "copper",
    "cove",
    "crest",
    "dapple",
    "dune",
    "ember",
    "fallow",
    "fennel",
    "fjord",
    "gable",
    "garnet",
    "glade",
    "gorse",
    "harbour",
    "hazel",
    "heath",
    "hollow",
    "indigo",
    "ivory",
    "juniper",
    "kestrel",
    "lantern",
    "larch",
    "linden",
    "marsh",
    "meadow",
    "mica",
    "moor",
    "nettle",
    "onyx",
    "orchard",
    "pebble",
    "quarry",
    "reed",
    "ridge",
    "rowan",
    "russet",
    "sable",
    "saffron",
    "sedge",
    "shale",
    "sorrel",
    "spruce",
    "tamarisk",
    "thicket",
    "topaz",
    "trellis",
    "umber",
    "vale",
    "willow",
    "wren",
    "yarrow",
    "zephyr",
)
_BASE = len(_WORDS)  # 64; 64**3 = 262,144 distinct references for 18,000 rows


def _reference(index: int) -> str:
    """A unique three-word reference for the *index*-th decoy in the corpus."""
    a, rest = divmod(index, _BASE * _BASE)
    b, c = divmod(rest, _BASE)
    return f"{_WORDS[a]} {_WORDS[b]} {_WORDS[c]}"


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Template:
    family: str
    evidence_type: str
    predicate: str
    weight: float
    pool: str
    money: bool
    render: Any


def _invoice(name: str, amount: Decimal, start: datetime, end: datetime, reference: str) -> str:
    return (
        f"Invoice from {name} for service {start:%d %B} through {end:%d %B %Y}. "
        f"Amount due USD {amount}. Account on file. Payment is due within 21 days "
        f"of the invoice date. Correspondence reference {reference}."
    )


def _confirmation(name: str, amount: Decimal, start: datetime, end: datetime, ref: str) -> str:
    del amount, end
    return (
        f"{name} confirms that your service request was received and processed on "
        f"{start:%d %B %Y}. No further action is required from you at this time. "
        f"Correspondence reference {ref}."
    )


def _cancellation(name: str, amount: Decimal, start: datetime, end: datetime, ref: str) -> str:
    del amount
    return (
        f"{name} has cancelled the service associated with your account. Service "
        f"ends on {end:%d %B %Y} and the account will close shortly after. "
        f"Requested on {start:%d %B %Y}. Correspondence reference {ref}."
    )


def _deposit_clause(name: str, amount: Decimal, start: datetime, end: datetime, ref: str) -> str:
    del start, end
    return (
        f"Deposit terms for the tenancy administered by {name}. A deposit of "
        f"USD {amount} is held and shall be returned, less any lawful itemised "
        f"deductions, within thirty days of the final inspection. Correspondence "
        f"reference {ref}."
    )


def _delivery(name: str, amount: Decimal, start: datetime, end: datetime, ref: str) -> str:
    del amount, end
    return (
        f"{name} will deliver your order on {start:%d %B %Y}. Someone must be "
        f"present to sign for the delivery. Correspondence reference {ref}."
    )


def _payroll(name: str, amount: Decimal, start: datetime, end: datetime, ref: str) -> str:
    del end
    return (
        f"Payroll advice from {name} for the period ending {start:%d %B %Y}. "
        f"A payment of USD {amount} has been made to your nominated account. "
        f"Correspondence reference {ref}."
    )


def _appointment(name: str, amount: Decimal, start: datetime, end: datetime, ref: str) -> str:
    del amount, end
    return (
        f"Reminder from {name}: your appointment is booked for {start:%d %B %Y}. "
        f"Please arrive ten minutes early. Correspondence reference {ref}."
    )


def _policy(name: str, amount: Decimal, start: datetime, end: datetime, ref: str) -> str:
    del start, end
    return (
        f"Policy excerpt issued by {name}. Claims must be notified within "
        f"fourteen days of the incident. The excess payable on any admitted claim "
        f"is USD {amount}. Correspondence reference {ref}."
    )


_TEMPLATES: tuple[_Template, ...] = (
    _Template("INVOICE", "INVOICE_LINE", "service_billing_period", 0.14, "ISP", True, _invoice),
    _Template("INVOICE", "INVOICE_LINE", "service_billing_period", 0.14, "UTILITY", True, _invoice),
    _Template(
        "CONFIRMATION", "CONFIRMATION", "request_confirmed", 0.14, "SERVICES", False, _confirmation
    ),
    _Template(
        "CANCELLATION",
        "CANCELLATION_NOTICE",
        "service_cancellation_requested",
        0.11,
        "ISP",
        False,
        _cancellation,
    ),
    _Template(
        "DEPOSIT",
        "POLICY_TERM_TEXT",
        "security_deposit_terms",
        0.11,
        "LANDLORD",
        True,
        _deposit_clause,
    ),
    _Template("DELIVERY", "STATEMENT", "delivery_scheduled", 0.12, "LOGISTICS", False, _delivery),
    _Template("PAYROLL", "PAYMENT_RECORD", "payment_received", 0.12, "EMPLOYER", True, _payroll),
    _Template(
        "APPOINTMENT", "STATEMENT", "appointment_reminder", 0.06, "SERVICES", False, _appointment
    ),
    _Template("POLICY", "POLICY_TERM_TEXT", "policy_excess", 0.06, "RETAIL", True, _policy),
)

_WEIGHTS: tuple[float, ...] = tuple(t.weight for t in _TEMPLATES)


# ---------------------------------------------------------------------------
# The decoy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Decoy:
    """One synthetic evidence row and the synthetic artifact it hangs off."""

    index: int
    bucket: str
    id: UUID
    artifact_id: UUID
    slug: str
    tenant_id: UUID
    user_id: UUID
    evidence_type: str
    predicate: str
    counterparty_name: str
    normalized_text: str
    observed_at: datetime
    valid_from: datetime | None
    valid_to: datetime | None
    currency: str | None
    amount: Decimal | None
    is_near_miss: bool

    def embedding_text(self) -> str:
        return build_embedding_text(
            evidence_type=self.evidence_type,
            counterparty_name=self.counterparty_name,
            predicate=self.predicate,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
            currency=self.currency,
            amount=self.amount,
            has_identifier=False,
            normalized_text=self.normalized_text,
        )

    def to_artifact(self) -> SeedArtifact:
        payload = self.normalized_text.encode("utf-8")
        return SeedArtifact(
            id=self.artifact_id,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            slug=f"decoy-artifact-{self.index:05d}",
            source_type="SEED_FIXTURE",
            s3_bucket=S3_BUCKET,
            s3_key=f"raw/{self.bucket}/decoys/{self.index:05d}.txt",
            content_sha256=hashlib.sha256(payload).digest(),
            size_bytes=len(payload),
            mime_type="text/plain",
            source_message_id=None,
            sender=None,
            sender_domain=None,
            recipient=None,
            subject=None,
            received_at=self.observed_at,
            event_time=self.observed_at,
            parser_status="PARSED",
        )

    def to_evidence(self) -> SeedEvidence:
        return SeedEvidence(
            id=self.id,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            artifact_id=self.artifact_id,
            slug=self.slug,
            evidence_type=self.evidence_type,
            normalized_text=self.normalized_text,
            exact_text=None,
            source_locator={"kind": "SEED_FIXTURE", "index": self.index},
            actor_ref=self.counterparty_name,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
            observed_at=self.observed_at,
            extraction_confidence=Decimal("0.85"),
            source_authority=Decimal("0.60"),
            counterparty_name=self.counterparty_name,
            predicate=self.predicate,
            currency=self.currency,
            amount=self.amount,
            has_identifier=False,
            case_slug=None,
        )


def _near_miss_indices() -> frozenset[int]:
    """Deterministic positions for the ``NEAR_MISS_QUOTA`` engineered rows.

    Spread evenly through the hero bucket rather than clustered at the front,
    so a loader bug that truncates the corpus removes near-misses in proportion
    instead of removing all or none of them.
    """
    stride = DECOY_PLAN["hero"] // NEAR_MISS_QUOTA
    return frozenset(i * stride for i in range(NEAR_MISS_QUOTA))


def _build() -> tuple[Decoy, ...]:
    rng = random.Random(RNG_SEED)
    near_misses = _near_miss_indices()
    decoys: list[Decoy] = []
    index = 0

    for bucket, count in DECOY_PLAN.items():
        user = user_of(bucket)
        for _ in range(count):
            is_near_miss = bucket == "hero" and index in near_misses

            if is_near_miss:
                template = _TEMPLATES[0]  # ISP invoice
                name = _ISP_POOL[rng.randrange(len(_ISP_POOL))]
            elif bucket == "hero":
                template = rng.choices(_TEMPLATES, weights=_WEIGHTS, k=1)[0]
                pool = _ALL_POOLS[template.pool]
                name = pool[rng.randrange(len(pool))]
            else:
                # The isolation tenants mirror the hero's vocabulary in roughly
                # two rows out of five, which is what makes a leak visible.
                template = rng.choices(_TEMPLATES, weights=_WEIGHTS, k=1)[0]
                if rng.random() < 0.4:
                    name = _ISO_MIRROR_NAMES[rng.randrange(len(_ISO_MIRROR_NAMES))]
                else:
                    pool = _ALL_POOLS[template.pool]
                    name = pool[rng.randrange(len(pool))]

            days_ago = rng.randint(1, LOOKBACK_DAYS - 1)
            observed_at = DEMO_ANCHOR_UTC - timedelta(
                days=days_ago, hours=rng.randint(0, 23), minutes=rng.randint(0, 59)
            )
            period_days = rng.randint(28, 31)
            period_start = observed_at - timedelta(days=period_days)
            period_end = observed_at

            if is_near_miss:
                amount = _near_miss_amount(rng)
            elif template.money:
                amount = Decimal(f"{rng.randint(1800, 34000) / 100:.2f}")
            else:
                amount = None

            reference = _reference(index)
            text = template.render(name, amount, period_start, period_end, reference)
            slug = f"decoy-{index:05d}"

            decoys.append(
                Decoy(
                    index=index,
                    bucket=bucket,
                    id=sid("evidence", slug),
                    artifact_id=sid("artifact", slug),
                    slug=slug,
                    tenant_id=user.tenant_id,
                    user_id=user.id,
                    evidence_type=template.evidence_type,
                    predicate=template.predicate,
                    counterparty_name=name,
                    normalized_text=" ".join(text.split()),
                    observed_at=observed_at,
                    valid_from=period_start if template.money else None,
                    valid_to=period_end if template.money else None,
                    currency="USD" if amount is not None else None,
                    amount=amount,
                    is_near_miss=is_near_miss,
                )
            )
            index += 1

    return tuple(decoys)


def _near_miss_amount(rng: random.Random) -> Decimal:
    """Within USD 25 of the hero invoice, and never equal to it.

    Equality would make the near-miss a *duplicate* rather than a near miss,
    and the identity gates would then be measuring the wrong thing: the correct
    behaviour for two identical amounts from different providers is decided by
    the identifier match in Stage B, not by ranking.
    """
    while True:
        cents = rng.randint(
            int((_HERO_INVOICE_AMOUNT - _NEAR_MISS_WINDOW) * 100),
            int((_HERO_INVOICE_AMOUNT + _NEAR_MISS_WINDOW) * 100),
        )
        amount = (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"))
        if amount != _HERO_INVOICE_AMOUNT:
            return amount


@lru_cache(maxsize=1)
def _corpus() -> tuple[Decoy, ...]:
    return _build()


def generate_decoys() -> Iterator[Decoy]:
    """The full 18,000-row corpus, in load order: hero, then ``iso-a``, ``iso-b``."""
    yield from _corpus()


def corpus_fingerprint() -> str:
    """A sha256 over every decoy's id and text.

    Two machines that disagree here disagree about the corpus, and every eval
    number computed against it is therefore incomparable. ``MANIFEST.json``
    records the value so the disagreement surfaces at the manifest check rather
    than in an unexplained metric drift.
    """
    digest = hashlib.sha256()
    for decoy in _corpus():
        digest.update(str(decoy.id).encode("ascii"))
        digest.update(b"\x00")
        digest.update(decoy.normalized_text.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def decoy_user_counts() -> dict[str, int]:
    """Rows per bucket, recomputed rather than restated."""
    counts = dict.fromkeys(DECOY_PLAN, 0)
    for decoy in _corpus():
        counts[decoy.bucket] += 1
    return counts


assert {u.slug for u in USERS} >= set(DECOY_PLAN), "every decoy bucket needs a seeded user"
