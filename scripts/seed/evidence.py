"""The 32 curated evidence items and their artifacts (``T2.8`` step 6).

Authority
---------
- ``docs/specs/10_DATABASE_DDL.md`` section 17.5 -- the per-case item counts
  and the highlighted items, transcribed exactly:
  7 / 4 / 5 / 3 / 5 / 2 / 3 / 2 / 1 = 32.
- ``docs/CANONICAL_DECISIONS.md`` -> Hero dataset canon.
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 12.1 -- identifiers are a flag,
  never embedded text, so ``NF-4471-8802`` appears in ``exact_text`` and in the
  artifact bytes and never in an embedding input.

The item deliberately absent
----------------------------
The June invoice for USD 186 is **not** here. Section 17.5: that artifact sits
in ``demo/artifacts/northline-june-invoice.eml`` and is uploaded live during the
demo. Seeding it would turn the reveal into a lookup and make the counterfactual
a fiction -- ``test_seed_canon.py`` asserts the string "186" appears in no
curated item.

What this module does *not* write
---------------------------------
``claims``, ``beliefs``, ``belief_versions`` and ``belief_support``. Section
17.5 says each curated item gets those "written through the real Kernel, not
through raw inserts", and ``10_DATABASE_DDL.md`` section 12 grants ``INSERT``
on all four to ``pv_kernel_writer`` alone. That is step 9, and step 9 needs
Phase 4. ``scripts/seed/loader.replay_curated_proposals`` reports the deferral
rather than substituting for it, and the obligation fixtures step 9 will replay
live in ``scripts/seed/obligations.py``.

The ``MemoryProposal`` fixtures themselves are **not** authored here. Building
nine of them against ``provenance_contracts.proposal`` -- ``ProposedClaim``,
``ProposedCommitment``, ``ProposedBeliefMutation``, ``ConflictHint``,
``ProposedTrigger`` and their cross-validators -- would freeze a guess at the
kernel's input shape four phases before the kernel that consumes it exists, and
a wrong guess would be discovered by rewriting the seed rather than by a failing
test. Phase 4 authors them next to the kernel that reads them.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from scripts.seed.artifacts import S3_BUCKET, ArtifactSource, content_sha256, render, s3_key
from scripts.seed.counterparties import (
    BELTLINE,
    CASCADE,
    HARBORVIEW,
    KESTREL,
    NORTHLINE,
)
from scripts.seed.ids import DEPOSIT_DUE_AT, sid
from scripts.seed.rows import SeedArtifact, SeedEvidence
from scripts.seed.tenants import HERO_TENANT, HERO_USER

__all__ = [
    "ARTIFACT_SOURCES",
    "CURATED_ARTIFACTS",
    "CURATED_EVIDENCE",
    "JUNE_INVOICE",
    "artifact_row",
    "evidence_of",
]

_HERO_ADDRESS = HERO_USER.email
_HERO_NAME = HERO_USER.display_name


def _at(days_from_due: int, hour: int = 9, minute: int = 0) -> datetime:
    """An instant offset from the deposit due date, which is itself an offset
    from ``DEMO_ANCHOR``. Nothing in this file names a year."""
    return (DEPOSIT_DUE_AT + timedelta(days=days_from_due)).replace(hour=hour, minute=minute)


def _eml(
    slug: str,
    counterparty_name: str,
    sender_local: str,
    sender_domain: str,
    subject: str,
    days_from_due: int,
    body: str,
    *,
    outbound: bool = False,
    hour: int = 9,
) -> ArtifactSource:
    if outbound:
        return ArtifactSource(
            slug=slug,
            filename=f"{slug}.eml",
            mime_type="message/rfc822",
            sender_name=_HERO_NAME,
            sender_address=_HERO_ADDRESS,
            recipient_name=counterparty_name,
            recipient_address=f"{sender_local}@{sender_domain}",
            subject=subject,
            received_at=_at(days_from_due, hour),
            body=body,
        )
    return ArtifactSource(
        slug=slug,
        filename=f"{slug}.eml",
        mime_type="message/rfc822",
        sender_name=counterparty_name,
        sender_address=f"{sender_local}@{sender_domain}",
        recipient_name=_HERO_NAME,
        recipient_address=_HERO_ADDRESS,
        subject=subject,
        received_at=_at(days_from_due, hour),
        body=body,
    )


def _pdf(
    slug: str,
    counterparty_name: str,
    sender_local: str,
    sender_domain: str,
    subject: str,
    days_from_due: int,
    body: str,
) -> ArtifactSource:
    return ArtifactSource(
        slug=slug,
        filename=f"{slug}.pdf",
        mime_type="application/pdf",
        sender_name=counterparty_name,
        sender_address=f"{sender_local}@{sender_domain}",
        recipient_name=_HERO_NAME,
        recipient_address=_HERO_ADDRESS,
        subject=subject,
        received_at=_at(days_from_due),
        body=body,
    )


NF_DOMAIN = NORTHLINE.canonical_domain
HPM_DOMAIN = HARBORVIEW.canonical_domain
BM_DOMAIN = BELTLINE.canonical_domain
KA_DOMAIN = KESTREL.canonical_domain
CP_DOMAIN = CASCADE.canonical_domain

# ---------------------------------------------------------------------------
# Artifact sources -- 31 for the curated corpus
# ---------------------------------------------------------------------------

ARTIFACT_SOURCES: tuple[ArtifactSource, ...] = (
    # --- case 1, old ISP service cancellation -----------------------------
    _eml(
        "northline-cancellation-request",
        NORTHLINE.display_name,
        "support",
        NF_DOMAIN,
        "Cancellation request for service at 214 Ridgeway Apt 3B",
        -32,
        """
        Please cancel internet service at 214 Ridgeway Apt 3B. I am moving out
        at the end of the month and will not need service at this address after
        that. Account reference NF-4471-8802. Please confirm the effective
        termination date in writing.
        """,
        outbound=True,
    ),
    _eml(
        "northline-cancellation-confirmation",
        NORTHLINE.display_name,
        "support",
        NF_DOMAIN,
        "Your cancellation is confirmed",
        -31,
        """
        We have received your cancellation request for internet service at
        214 Ridgeway Apt 3B on account NF-4471-8802. Service termination is
        effective 31 May 2026. No further monthly charges will be raised on this
        account after the termination date. Equipment must be returned within
        fourteen days.
        """,
    ),
    _eml(
        "northline-final-bill-notice",
        NORTHLINE.display_name,
        "billing",
        NF_DOMAIN,
        "Your final bill is being prepared",
        -26,
        """
        A final bill covering service up to the termination date will be issued
        shortly. It will cover the period to 31 May 2026 only, prorated where
        applicable, and no charges will follow it.
        """,
    ),
    _eml(
        "northline-equipment-return",
        NORTHLINE.display_name,
        "returns",
        NF_DOMAIN,
        "Equipment return received",
        -24,
        """
        We have received the returned router and set-top box for the service at
        214 Ridgeway Apt 3B. Nothing further is outstanding on the equipment
        return, and no unreturned-equipment fee will be raised.
        """,
    ),
    _eml(
        "northline-closure-email",
        NORTHLINE.display_name,
        "support",
        NF_DOMAIN,
        "Account closed",
        -14,
        """
        The service account for 214 Ridgeway Apt 3B is now closed. Service ended
        on 31 May 2026 as previously confirmed and the account is no longer
        active.
        """,
    ),
    _eml(
        "northline-account-status-snapshot",
        NORTHLINE.display_name,
        "noreply",
        NF_DOMAIN,
        "Account status summary",
        -13,
        """
        Account status summary: service state TERMINATED, effective 31 May 2026.
        Billing state CLOSED. There are no scheduled future charges on this
        account.
        """,
    ),
    # --- case 2, old ISP final bill reconciliation -------------------------
    _eml(
        "northline-final-invoice",
        NORTHLINE.display_name,
        "billing",
        NF_DOMAIN,
        "Final invoice",
        -26,
        """
        Final invoice for internet service at 214 Ridgeway Apt 3B covering
        1 May 2026 through 31 May 2026. Amount due USD 74.20, payable within
        twenty-one days. This is the final invoice for this account.
        """,
        hour=11,
    ),
    _eml(
        "northline-payment-confirmation",
        NORTHLINE.display_name,
        "billing",
        NF_DOMAIN,
        "Payment received",
        -22,
        """
        We have received your payment of USD 74.20 against the final invoice for
        the service at 214 Ridgeway Apt 3B. Thank you.
        """,
    ),
    _eml(
        "northline-zero-balance-statement",
        NORTHLINE.display_name,
        "billing",
        NF_DOMAIN,
        "Statement: zero balance",
        -10,
        """
        Statement for the closed service account at 214 Ridgeway Apt 3B. Balance
        outstanding USD 0.00. Nothing is owed on this account.
        """,
    ),
    _eml(
        "northline-closure-acknowledgement",
        NORTHLINE.display_name,
        "support",
        NF_DOMAIN,
        "Closure acknowledgement",
        -10,
        """
        This message acknowledges that the billing relationship for the service
        at 214 Ridgeway Apt 3B is concluded and the account requires no further
        action from you.
        """,
        hour=15,
    ),
    # --- case 3, landlord deposit return ----------------------------------
    _pdf(
        "harborview-lease-deposit-clause",
        HARBORVIEW.display_name,
        "leasing",
        HPM_DOMAIN,
        "Residential lease extract - security deposit",
        -400,
        """
        Clause 14, security deposit. The tenant has lodged a security deposit of
        USD 1,800.00 with the landlord. The landlord shall return the deposit,
        less any lawful deductions itemised in writing, within thirty days of
        the final move-out inspection.
        """,
    ),
    _eml(
        "harborview-inspection-completion",
        HARBORVIEW.display_name,
        "property",
        HPM_DOMAIN,
        "Final inspection completed",
        -30,
        """
        The final move-out inspection at 214 Ridgeway Apt 3B was completed today,
        16 May 2026. The unit was found in good condition with no deductions
        proposed against the security deposit.
        """,
        hour=16,
    ),
    _eml(
        "harborview-deposit-promise",
        HARBORVIEW.display_name,
        "property",
        HPM_DOMAIN,
        "Deposit return timeline",
        -30,
        """
        Following today's inspection we will return your security deposit of
        USD 1,800.00 in full within 30 days of the inspection. No deductions
        will be applied.
        """,
        hour=17,
    ),
    _eml(
        "harborview-followup",
        HARBORVIEW.display_name,
        "property",
        HPM_DOMAIN,
        "Following up on the deposit",
        5,
        """
        Following up on the security deposit for 214 Ridgeway Apt 3B. The thirty
        day window from the inspection has now passed and the deposit has not
        been received. Please confirm when the funds will be sent.
        """,
        outbound=True,
    ),
    _eml(
        "harborview-no-response-note",
        HARBORVIEW.display_name,
        "property",
        HPM_DOMAIN,
        "Second follow-up on the deposit",
        10,
        """
        A second request regarding the outstanding security deposit for
        214 Ridgeway Apt 3B. No reply has been received to the previous message
        and the deposit remains outstanding.
        """,
        outbound=True,
    ),
    # --- case 4, landlord final inspection --------------------------------
    _eml(
        "harborview-inspection-scheduling",
        HARBORVIEW.display_name,
        "property",
        HPM_DOMAIN,
        "Scheduling your move-out inspection",
        -40,
        """
        We can schedule the move-out inspection for 214 Ridgeway Apt 3B on the
        morning of 16 May 2026. Please confirm that the unit will be empty and
        accessible.
        """,
    ),
    _pdf(
        "harborview-walkthrough-report",
        HARBORVIEW.display_name,
        "property",
        HPM_DOMAIN,
        "Move-out walkthrough report",
        -30,
        """
        Move-out walkthrough report for 214 Ridgeway Apt 3B.
        Kitchen: no damage. Bathroom: no damage. Living area: normal wear.
        Bedroom: normal wear. Keys collected: two. Deductions proposed: none.
        """,
    ),
    _eml(
        "harborview-key-handover",
        HARBORVIEW.display_name,
        "property",
        HPM_DOMAIN,
        "Keys received",
        -30,
        """
        We confirm receipt of two keys and one building fob for 214 Ridgeway
        Apt 3B. The tenancy is concluded as of the inspection date.
        """,
        hour=18,
    ),
    # --- case 5, movers damage reimbursement -------------------------------
    _eml(
        "beltline-damage-report",
        BELTLINE.display_name,
        "claims",
        BM_DOMAIN,
        "Damage reported on job 88214",
        -34,
        """
        Thank you for reporting the damage to the dining table and one bookcase
        during job 88214. A claims assessor has recorded the damage and will
        respond with a reimbursement decision.
        """,
    ),
    _eml(
        "beltline-reimbursement-promise",
        BELTLINE.display_name,
        "claims",
        BM_DOMAIN,
        "Reimbursement approved for job 88214",
        -28,
        """
        We have assessed the damage reported on job 88214 and approved
        reimbursement of USD 420.00. Payment will be issued to you directly.
        """,
    ),
    _eml(
        "beltline-partial-payment-receipt",
        BELTLINE.display_name,
        "accounts",
        BM_DOMAIN,
        "Payment sent",
        -21,
        """
        A bank transfer of USD 200.00 has been sent to you as a first payment
        against the approved damage reimbursement for job 88214.
        """,
    ),
    _eml(
        "beltline-partial-payment-ack",
        BELTLINE.display_name,
        "accounts",
        BM_DOMAIN,
        "Partial payment acknowledged",
        -20,
        """
        Acknowledging receipt of the USD 200.00 transfer against the damage
        reimbursement for job 88214. The remainder is still expected.
        """,
        outbound=True,
    ),
    _eml(
        "beltline-outstanding-balance",
        BELTLINE.display_name,
        "accounts",
        BM_DOMAIN,
        "Remaining balance on job 88214",
        -2,
        """
        The remaining balance on the approved damage reimbursement for job 88214
        is USD 220.00. We will confirm a payment date shortly.
        """,
    ),
    # --- case 6, movers scheduling dispute ---------------------------------
    _eml(
        "beltline-rescheduling-notice",
        BELTLINE.display_name,
        "dispatch",
        BM_DOMAIN,
        "Your move has been rescheduled",
        -52,
        """
        Your booked move on job 88214 has been rescheduled by one day because of
        vehicle availability. The crew will now arrive on the following morning.
        """,
    ),
    _eml(
        "beltline-arrival-confirmation",
        BELTLINE.display_name,
        "dispatch",
        BM_DOMAIN,
        "Crew arrival confirmed",
        -44,
        """
        The crew for job 88214 arrived on site as rescheduled and the move was
        completed the same day.
        """,
    ),
    # --- case 7, employer relocation reimbursement -------------------------
    _eml(
        "kestrel-expense-submission",
        KESTREL.display_name,
        "people-ops",
        KA_DOMAIN,
        "Relocation expense claim received",
        -38,
        """
        Your relocation expense claim under the Kestrel Analytics relocation
        programme has been received and is with your manager for approval.
        """,
    ),
    _eml(
        "kestrel-expense-approval",
        KESTREL.display_name,
        "people-ops",
        KA_DOMAIN,
        "Relocation expense claim approved",
        -30,
        """
        Your relocation expense claim has been approved in full. The approved
        amount is USD 2,350.00 and it will be paid with your next salary run.
        """,
    ),
    _eml(
        "kestrel-reimbursement-received",
        KESTREL.display_name,
        "payroll",
        KA_DOMAIN,
        "Relocation reimbursement paid",
        -18,
        """
        A relocation reimbursement of USD 2,350.00 was included in your salary
        payment. Nothing remains outstanding on this claim.
        """,
    ),
    # --- case 8, employer temporary housing stipend ------------------------
    _eml(
        "kestrel-stipend-approval",
        KESTREL.display_name,
        "people-ops",
        KA_DOMAIN,
        "Temporary housing stipend approved",
        -46,
        """
        Your temporary housing stipend has been approved for the four weeks
        either side of the move date.
        """,
    ),
    _eml(
        "kestrel-stipend-payment",
        KESTREL.display_name,
        "payroll",
        KA_DOMAIN,
        "Temporary housing stipend paid",
        -36,
        """
        The approved temporary housing stipend has been paid in full. No further
        payment is due under this stipend.
        """,
    ),
    # --- case 9, new address installation credit ---------------------------
    _eml(
        "northline-new-install-credit-terms",
        NORTHLINE.display_name,
        "offers",
        NF_DOMAIN,
        "Your installation credit",
        -12,
        """
        Welcome to your new address at 88 Larkin on account NF-9913-2250. A
        promotional installation credit applies to the first bill on this
        account, provided the service remains active for ninety days.
        """,
    ),
)

#: The artifact that is **not** seeded: uploaded live during the demo. It lands
#: in ``demo/artifacts/`` with everything else so the demo has real bytes to
#: post, and ``test_seed_canon.py`` asserts it is on disk and carries the USD
#: 186.00 figure the whole reveal turns on.
JUNE_INVOICE = ArtifactSource(
    slug="northline-june-invoice",
    filename="northline-june-invoice.eml",
    mime_type="message/rfc822",
    sender_name=NORTHLINE.display_name,
    sender_address=f"billing@{NF_DOMAIN}",
    recipient_name=_HERO_NAME,
    recipient_address=_HERO_ADDRESS,
    subject="Invoice for June service",
    received_at=_at(-1, 8),
    body="""
    Invoice for internet service on account NF-4471-8802 covering
    1 June 2026 through 30 June 2026. Amount due USD 186.00 by 30 June 2026.
    Payment is due within twenty-one days of the invoice date.
    """,
)


# ---------------------------------------------------------------------------
# source_artifacts rows
# ---------------------------------------------------------------------------


def artifact_row(source: ArtifactSource) -> SeedArtifact:
    """The ``source_artifacts`` row for *source*, hashed from its real bytes."""
    payload = render(source)
    return SeedArtifact(
        id=sid("artifact", source.slug),
        tenant_id=HERO_TENANT.id,
        user_id=HERO_USER.id,
        slug=source.slug,
        source_type="EMAIL_INBOUND" if source.mime_type == "message/rfc822" else "UPLOAD_PDF",
        s3_bucket=S3_BUCKET,
        s3_key=s3_key(source, HERO_TENANT.slug, HERO_USER.slug),
        content_sha256=content_sha256(source),
        size_bytes=len(payload),
        mime_type=source.mime_type,
        source_message_id=source.message_id,
        sender=source.sender_address,
        sender_domain=source.sender_domain,
        recipient=source.recipient_address,
        subject=source.subject,
        received_at=source.received_at,
        event_time=source.received_at,
        filename=source.filename,
    )


CURATED_ARTIFACTS: tuple[SeedArtifact, ...] = tuple(artifact_row(s) for s in ARTIFACT_SOURCES)

_ARTIFACT_BY_SLUG = {a.slug: a for a in CURATED_ARTIFACTS}


# ---------------------------------------------------------------------------
# evidence_items rows -- 32
# ---------------------------------------------------------------------------


def _ev(
    slug: str,
    artifact_slug: str,
    case_slug: str,
    evidence_type: str,
    normalized_text: str,
    *,
    counterparty: str | None,
    predicate: str,
    days_from_due: int,
    valid_from_days: int | None = None,
    valid_to_days: int | None = None,
    currency: str | None = None,
    amount: str | None = None,
    has_identifier: bool = False,
    confidence: str = "0.96",
    authority: str = "0.90",
    exact_text: str | None = None,
) -> SeedEvidence:
    return SeedEvidence(
        id=sid("evidence", slug),
        tenant_id=HERO_TENANT.id,
        user_id=HERO_USER.id,
        artifact_id=_ARTIFACT_BY_SLUG[artifact_slug].id,
        slug=slug,
        evidence_type=evidence_type,
        normalized_text=" ".join(normalized_text.split()),
        exact_text=exact_text,
        source_locator={"kind": "EMAIL_BODY", "artifact": artifact_slug, "part": "1"},
        actor_ref=counterparty,
        valid_from=None if valid_from_days is None else _at(valid_from_days),
        valid_to=None if valid_to_days is None else _at(valid_to_days),
        observed_at=_at(days_from_due),
        extraction_confidence=Decimal(confidence),
        source_authority=Decimal(authority),
        counterparty_name=counterparty,
        predicate=predicate,
        currency=currency,
        amount=None if amount is None else Decimal(amount),
        has_identifier=has_identifier,
        case_slug=case_slug,
    )


CURATED_EVIDENCE: tuple[SeedEvidence, ...] = (
    # --- case 1 (7) --------------------------------------------------------
    _ev(
        "isp-cancellation-request",
        "northline-cancellation-request",
        "isp-cancellation",
        "CANCELLATION_NOTICE",
        """Customer requested cancellation of internet service at 214 Ridgeway
        Apt 3B, asking for written confirmation of the effective termination
        date.""",
        counterparty="Northline Fiber",
        predicate="service_cancellation_requested",
        days_from_due=-32,
        has_identifier=True,
        exact_text="Please cancel internet service at 214 Ridgeway Apt 3B. Account reference NF-4471-8802.",
    ),
    _ev(
        "isp-cancellation-confirmed",
        "northline-cancellation-confirmation",
        "isp-cancellation",
        "CONFIRMATION",
        """Provider confirmed receipt of the cancellation request for the
        service at 214 Ridgeway Apt 3B and stated no further monthly charges
        would be raised after termination.""",
        counterparty="Northline Fiber",
        predicate="cancellation_confirmed",
        days_from_due=-31,
        has_identifier=True,
    ),
    _ev(
        "isp-termination-effective-31-may",
        "northline-cancellation-confirmation",
        "isp-cancellation",
        "DATE_ASSERTION",
        """Provider stated that service termination is effective 31 May 2026 and
        that no further monthly charges arise on the account after that
        date.""",
        counterparty="Northline Fiber",
        predicate="service_termination_effective_date",
        days_from_due=-31,
        valid_from_days=-15,
        has_identifier=True,
        confidence="0.98",
        authority="0.95",
        exact_text="Service termination is effective 31 May 2026.",
    ),
    _ev(
        "isp-final-bill-notice",
        "northline-final-bill-notice",
        "isp-cancellation",
        "STATEMENT",
        """Provider stated a final bill covering service up to the termination
        date would be issued, and that no charges would follow it.""",
        counterparty="Northline Fiber",
        predicate="final_bill_announced",
        days_from_due=-26,
    ),
    _ev(
        "isp-equipment-return-receipt",
        "northline-equipment-return",
        "isp-cancellation",
        "RECEIPT",
        """Provider confirmed receipt of returned equipment for the service at
        214 Ridgeway Apt 3B with no unreturned-equipment fee.""",
        counterparty="Northline Fiber",
        predicate="equipment_returned",
        days_from_due=-24,
    ),
    _ev(
        "isp-closure-email",
        "northline-closure-email",
        "isp-cancellation",
        "SERVICE_STATUS_ASSERTION",
        """Provider stated the service account for 214 Ridgeway Apt 3B is closed
        and service ended on 31 May 2026.""",
        counterparty="Northline Fiber",
        predicate="service_status",
        days_from_due=-14,
        valid_from_days=-15,
        confidence="0.97",
    ),
    _ev(
        "isp-account-status-snapshot",
        "northline-account-status-snapshot",
        "isp-cancellation",
        "SERVICE_STATUS_ASSERTION",
        """Account status summary reported service state TERMINATED effective
        31 May 2026, billing state CLOSED, and no scheduled future charges.""",
        counterparty="Northline Fiber",
        predicate="service_status",
        days_from_due=-13,
        valid_from_days=-15,
    ),
    # --- case 2 (4) --------------------------------------------------------
    _ev(
        "isp-final-invoice",
        "northline-final-invoice",
        "isp-final-bill",
        "INVOICE_LINE",
        """Final invoice for internet service at 214 Ridgeway Apt 3B covering
        1 May 2026 through 31 May 2026, payable within twenty-one days.""",
        counterparty="Northline Fiber",
        predicate="service_billing_period",
        days_from_due=-26,
        valid_from_days=-45,
        valid_to_days=-15,
        currency="USD",
        amount="74.20",
        has_identifier=True,
    ),
    _ev(
        "isp-final-invoice-paid",
        "northline-payment-confirmation",
        "isp-final-bill",
        "PAYMENT_RECORD",
        """Provider confirmed receipt of payment against the final invoice for
        the service at 214 Ridgeway Apt 3B.""",
        counterparty="Northline Fiber",
        predicate="payment_received",
        days_from_due=-22,
        currency="USD",
        amount="74.20",
    ),
    _ev(
        "isp-zero-balance-statement",
        "northline-zero-balance-statement",
        "isp-final-bill",
        "STATEMENT",
        """Statement for the closed service account reported a zero outstanding
        balance and nothing owed.""",
        counterparty="Northline Fiber",
        predicate="account_balance",
        days_from_due=-10,
        currency="USD",
        amount="0.00",
    ),
    _ev(
        "isp-closure-acknowledgement",
        "northline-closure-acknowledgement",
        "isp-final-bill",
        "CONFIRMATION",
        """Provider acknowledged the billing relationship for the service at
        214 Ridgeway Apt 3B is concluded and requires no further action.""",
        counterparty="Northline Fiber",
        predicate="billing_relationship_concluded",
        days_from_due=-10,
    ),
    # --- case 3 (5) --------------------------------------------------------
    _ev(
        "deposit-lease-clause",
        "harborview-lease-deposit-clause",
        "landlord-deposit",
        "POLICY_TERM_TEXT",
        """Lease clause fourteen records a security deposit lodged with the
        landlord, returnable less lawful itemised deductions within thirty days
        of the final move-out inspection.""",
        counterparty="Harborview Property Management",
        predicate="security_deposit_terms",
        days_from_due=-400,
        currency="USD",
        amount="1800.00",
        has_identifier=True,
        authority="0.98",
    ),
    _ev(
        "deposit-inspection-completed",
        "harborview-inspection-completion",
        "landlord-deposit",
        "CONFIRMATION",
        """Landlord confirmed the final move-out inspection at 214 Ridgeway Apt
        3B was completed on 16 May 2026 with no deductions proposed.""",
        counterparty="Harborview Property Management",
        predicate="inspection_completed",
        days_from_due=-30,
        valid_from_days=-30,
        confidence="0.98",
    ),
    _ev(
        "deposit-thirty-day-promise",
        "harborview-deposit-promise",
        "landlord-deposit",
        "COMMITMENT_STATEMENT",
        """Landlord promised to return the security deposit in full within
        thirty days of the inspection, with no deductions applied.""",
        counterparty="Harborview Property Management",
        predicate="deposit_return_promise",
        days_from_due=-30,
        valid_from_days=-30,
        currency="USD",
        amount="1800.00",
        confidence="0.98",
        authority="0.95",
        exact_text="we will return your security deposit of USD 1,800.00 in full within 30 days of the inspection",
    ),
    _ev(
        "deposit-followup",
        "harborview-followup",
        "landlord-deposit",
        "STATEMENT",
        """Customer followed up because the thirty day window had passed and the
        deposit had not been received.""",
        counterparty="Harborview Property Management",
        predicate="deposit_outstanding",
        days_from_due=5,
    ),
    _ev(
        "deposit-no-response",
        "harborview-no-response-note",
        "landlord-deposit",
        "STATEMENT",
        """A second request recorded that no reply had been received and the
        deposit remained outstanding.""",
        counterparty="Harborview Property Management",
        predicate="deposit_outstanding",
        days_from_due=10,
    ),
    # --- case 4 (3) --------------------------------------------------------
    _ev(
        "inspection-scheduling",
        "harborview-inspection-scheduling",
        "landlord-inspection",
        "STATEMENT",
        """Landlord proposed a move-out inspection for 214 Ridgeway Apt 3B on
        the morning of 16 May 2026.""",
        counterparty="Harborview Property Management",
        predicate="inspection_scheduled",
        days_from_due=-40,
    ),
    _ev(
        "inspection-walkthrough-report",
        "harborview-walkthrough-report",
        "landlord-inspection",
        "STATEMENT",
        """Move-out walkthrough report recorded no damage in the kitchen or
        bathroom, normal wear elsewhere, two keys collected, and no deductions
        proposed.""",
        counterparty="Harborview Property Management",
        predicate="inspection_outcome",
        days_from_due=-30,
        authority="0.95",
    ),
    _ev(
        "inspection-key-handover",
        "harborview-key-handover",
        "landlord-inspection",
        "CONFIRMATION",
        """Landlord confirmed receipt of two keys and one building fob and that
        the tenancy concluded on the inspection date.""",
        counterparty="Harborview Property Management",
        predicate="tenancy_concluded",
        days_from_due=-30,
        valid_from_days=-30,
    ),
    # --- case 5 (5) --------------------------------------------------------
    _ev(
        "damage-report",
        "beltline-damage-report",
        "movers-damage",
        "STATEMENT",
        """Mover recorded damage to a dining table and a bookcase during the
        move and referred it to a claims assessor.""",
        counterparty="Beltline Movers",
        predicate="damage_reported",
        days_from_due=-34,
        has_identifier=True,
    ),
    _ev(
        "damage-reimbursement-promise",
        "beltline-reimbursement-promise",
        "movers-damage",
        "COMMITMENT_STATEMENT",
        """Mover approved reimbursement for the assessed damage and stated that
        payment would be issued to the customer directly.""",
        counterparty="Beltline Movers",
        predicate="damage_reimbursement_promise",
        days_from_due=-28,
        currency="USD",
        amount="420.00",
        has_identifier=True,
        confidence="0.97",
        authority="0.92",
        exact_text="approved reimbursement of USD 420.00",
    ),
    _ev(
        "damage-partial-payment",
        "beltline-partial-payment-receipt",
        "movers-damage",
        "PAYMENT_RECORD",
        """Mover sent a bank transfer as a first payment against the approved
        damage reimbursement.""",
        counterparty="Beltline Movers",
        predicate="payment_sent",
        days_from_due=-21,
        currency="USD",
        amount="200.00",
        confidence="0.98",
    ),
    _ev(
        "damage-partial-payment-ack",
        "beltline-partial-payment-ack",
        "movers-damage",
        "CONFIRMATION",
        """Customer acknowledged the partial transfer against the damage
        reimbursement and recorded that a remainder was still expected.""",
        counterparty="Beltline Movers",
        predicate="payment_acknowledged",
        days_from_due=-20,
        currency="USD",
        amount="200.00",
    ),
    _ev(
        "damage-outstanding-balance",
        "beltline-outstanding-balance",
        "movers-damage",
        "AMOUNT_ASSERTION",
        """Mover stated the remaining balance on the approved damage
        reimbursement and undertook to confirm a payment date.""",
        counterparty="Beltline Movers",
        predicate="outstanding_balance",
        days_from_due=-2,
        currency="USD",
        amount="220.00",
        has_identifier=True,
    ),
    # --- case 6 (2) --------------------------------------------------------
    _ev(
        "scheduling-rescheduled",
        "beltline-rescheduling-notice",
        "movers-scheduling",
        "STATEMENT",
        """Mover rescheduled the booked move by one day because of vehicle
        availability.""",
        counterparty="Beltline Movers",
        predicate="move_rescheduled",
        days_from_due=-52,
        has_identifier=True,
    ),
    _ev(
        "scheduling-arrival-confirmed",
        "beltline-arrival-confirmation",
        "movers-scheduling",
        "CONFIRMATION",
        """Mover confirmed the crew arrived as rescheduled and the move was
        completed the same day.""",
        counterparty="Beltline Movers",
        predicate="move_completed",
        days_from_due=-44,
    ),
    # --- case 7 (3) --------------------------------------------------------
    _ev(
        "relocation-expense-submitted",
        "kestrel-expense-submission",
        "employer-relocation",
        "STATEMENT",
        """Employer acknowledged receipt of a relocation expense claim under the
        relocation programme and routed it for approval.""",
        counterparty="Kestrel Analytics",
        predicate="expense_claim_submitted",
        days_from_due=-38,
    ),
    _ev(
        "relocation-expense-approved",
        "kestrel-expense-approval",
        "employer-relocation",
        "CONFIRMATION",
        """Employer approved the relocation expense claim in full and stated the
        approved amount would be paid with the next salary run.""",
        counterparty="Kestrel Analytics",
        predicate="expense_claim_approved",
        days_from_due=-30,
        currency="USD",
        amount="2350.00",
        confidence="0.98",
        authority="0.94",
    ),
    _ev(
        "relocation-reimbursement-received",
        "kestrel-reimbursement-received",
        "employer-relocation",
        "PAYMENT_RECORD",
        """Employer paid the relocation reimbursement with the salary payment
        and stated nothing remained outstanding on the claim.""",
        counterparty="Kestrel Analytics",
        predicate="payment_received",
        days_from_due=-18,
        currency="USD",
        amount="2350.00",
        confidence="0.99",
    ),
    # --- case 8 (2) --------------------------------------------------------
    _ev(
        "stipend-approved",
        "kestrel-stipend-approval",
        "employer-stipend",
        "CONFIRMATION",
        """Employer approved a temporary housing stipend for the four weeks
        either side of the move date.""",
        counterparty="Kestrel Analytics",
        predicate="stipend_approved",
        days_from_due=-46,
    ),
    _ev(
        "stipend-paid",
        "kestrel-stipend-payment",
        "employer-stipend",
        "PAYMENT_RECORD",
        """Employer paid the approved temporary housing stipend in full with no
        further payment due.""",
        counterparty="Kestrel Analytics",
        predicate="payment_received",
        days_from_due=-36,
    ),
    # --- case 9 (1) --------------------------------------------------------
    _ev(
        "new-install-credit-terms",
        "northline-new-install-credit-terms",
        "new-install-credit",
        "POLICY_TERM_TEXT",
        """Provider described a promotional installation credit applying to the
        first bill at the new address, conditional on the service remaining
        active for ninety days.""",
        counterparty="Northline Fiber",
        predicate="promotional_credit_terms",
        days_from_due=-12,
        has_identifier=True,
    ),
)

_EVIDENCE_BY_SLUG = {e.slug: e for e in CURATED_EVIDENCE}


def evidence_of(slug: str) -> SeedEvidence:
    return _EVIDENCE_BY_SLUG[slug]
