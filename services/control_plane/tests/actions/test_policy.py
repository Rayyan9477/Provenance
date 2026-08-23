"""The recipient allowlist and the kill switch -- both default closed.

``G9.5``: "the recipient allowlist is real: ``RECIPIENT_NOT_ALLOWLISTED``, zero
provider calls."
``G9.6``: ``PV_ACTION_EXECUTION_MODE=DISABLED`` "records approvals and sends
nothing".

Why the default matters more than the mechanism
------------------------------------------------
``PV_ACTION_ALLOWLIST`` defaults to the empty string, and
``Settings.action_allowlist_addresses`` parses that to ``()``. Empty means
**nothing is allowed**, not "no restriction". A demo that could reach a real
counterparty through a forgotten environment variable is a defect, and the
shape that prevents it is a default, not a runbook step.

The two knobs are separate on purpose. ``ACTION_RECIPIENT_MODE`` decides where
a permitted message is *delivered*; the allowlist decides whether it may be
sent at all. Collapsing them would mean turning on ``COUNTERPARTY`` mode
silently widened the allowlist.
"""

from __future__ import annotations

import pytest

from services.control_plane.app.actions.policy import ActionPolicy

pytestmark = pytest.mark.unit


# ==========================================================================
# Default closed
# ==========================================================================


def test_the_default_policy_allows_no_recipient_at_all() -> None:
    """An empty allowlist is a refusal, never a wildcard."""
    policy = ActionPolicy()

    assert policy.allowlist == frozenset()
    assert policy.recipient_allowlisted("billing@northlinefiber.example") is False
    assert policy.recipient_allowlisted("anyone@anywhere.example") is False


def test_a_settings_object_with_no_allowlist_produces_a_closed_policy() -> None:
    """``Settings.pv_action_allowlist`` defaults to ``""``.

    Read through the same property the application reads, so the test fails if
    the default is ever widened in ``provenance_contracts.settings``.
    """
    settings = _FakeSettings(pv_action_allowlist="")

    policy = ActionPolicy.from_settings(settings)

    assert policy.allowlist == frozenset()
    assert policy.recipient_allowlisted("billing@northlinefiber.example") is False


# ==========================================================================
# What an allowlist entry means
# ==========================================================================


def test_a_full_address_entry_matches_only_that_address(hero) -> None:
    policy = ActionPolicy(allowlist=frozenset({hero.recipient}))

    assert policy.recipient_allowlisted(hero.recipient) is True
    assert policy.recipient_allowlisted("someone-else@northlinefiber.example") is False


def test_a_bare_domain_entry_matches_every_address_in_that_domain() -> None:
    """Section 9.8: the allowlist is the counterparty's ``canonical_domain``
    plus ``demo-sink.provenance.app``. Both are domains, not addresses."""
    policy = ActionPolicy(allowlist=frozenset({"demo-sink.provenance.app"}))

    assert policy.recipient_allowlisted("anything@demo-sink.provenance.app") is True
    assert policy.recipient_allowlisted("anything@demo-sink.provenance.app.evil.example") is False


def test_matching_is_case_insensitive_and_whitespace_tolerant(hero) -> None:
    """Email domains are case-insensitive and runbook lists carry spaces.

    A case-sensitive comparison would refuse a correctly configured recipient,
    and the operator's fix would be to widen the list.
    """
    policy = ActionPolicy.from_settings(
        _FakeSettings(pv_action_allowlist=f" {hero.recipient.upper()} , demo-sink.provenance.app ")
    )

    assert policy.recipient_allowlisted(hero.recipient) is True
    assert policy.recipient_allowlisted("Alex@Demo-Sink.Provenance.App") is True


def test_a_malformed_recipient_is_refused_rather_than_parsed_generously() -> None:
    """No ``@``, two ``@``, or an empty local part is not an address."""
    policy = ActionPolicy(allowlist=frozenset({"northlinefiber.example"}))

    assert policy.recipient_allowlisted("northlinefiber.example") is False
    assert policy.recipient_allowlisted("a@b@northlinefiber.example") is False
    assert policy.recipient_allowlisted("@northlinefiber.example") is False


def test_an_internal_reminder_has_no_recipient_to_allowlist() -> None:
    """``ck_action_intents_recipient`` permits ``NULL`` only for
    ``INTERNAL_REMINDER``. An action with no recipient reaches nobody, so the
    allowlist has nothing to decide."""
    assert ActionPolicy().recipient_allowlisted(None) is True


# ==========================================================================
# Delivery: DEMO_SINK contains, it does not authorise
# ==========================================================================


def test_demo_sink_mode_redirects_delivery_without_widening_the_allowlist(hero) -> None:
    """The counterparty address is what is checked; the sink is where it goes.

    ``ACTION_RECIPIENT_MODE=DEMO_SINK`` is containment. It must not be able to
    turn an unallowlisted recipient into a permitted one by rewriting it into
    a domain that happens to be allowed.
    """
    policy = ActionPolicy(
        allowlist=frozenset({hero.recipient}),
        recipient_mode="DEMO_SINK",
        demo_sink_domain="demo-sink.provenance.app",
    )

    assert policy.delivery_address(hero.recipient) == "billing@demo-sink.provenance.app"
    assert policy.recipient_allowlisted(hero.off_allowlist_recipient) is False


def test_counterparty_mode_delivers_to_the_recipient_itself(hero) -> None:
    policy = ActionPolicy(
        allowlist=frozenset({hero.recipient}),
        recipient_mode="COUNTERPARTY",
        demo_sink_domain="demo-sink.provenance.app",
    )

    assert policy.delivery_address(hero.recipient) == hero.recipient


def test_demo_sink_mode_with_no_sink_domain_configured_does_not_invent_one(hero) -> None:
    """``SES_DEMO_SINK_DOMAIN`` is optional and defaults to ``None``.

    Guessing a domain would send a real message to a domain nobody owns, which
    is a worse outcome than delivering to the address that was allowlisted.
    """
    policy = ActionPolicy(allowlist=frozenset({hero.recipient}), recipient_mode="DEMO_SINK")

    assert policy.delivery_address(hero.recipient) == hero.recipient


# ==========================================================================
# The kill switch
# ==========================================================================


def test_execution_is_enabled_by_default_and_disabled_by_the_switch() -> None:
    """``PV_ACTION_EXECUTION_MODE`` is the documented rollback position.

    ``ops/gates/PHASE_09.md``: roll back to the ``G-8`` commit; set
    ``PV_ACTION_EXECUTION_MODE=DISABLED``. Approvals continue to be recorded
    and nothing is sent.
    """
    assert ActionPolicy().execution_enabled is True
    assert ActionPolicy(execution_mode="DISABLED").execution_enabled is False


def test_the_switch_is_read_from_settings() -> None:
    settings = _FakeSettings(pv_action_execution_mode="DISABLED")

    assert ActionPolicy.from_settings(settings).execution_enabled is False


class _FakeSettings:
    """The four fields :meth:`ActionPolicy.from_settings` reads, and no more.

    A real ``Settings`` would demand a platform, a database URL and a model id
    to construct, none of which has anything to do with whether a recipient is
    allowlisted. The narrowness is the assertion: if ``from_settings`` ever
    reaches for a fifth field, this fake stops satisfying it.
    """

    def __init__(
        self,
        *,
        pv_action_allowlist: str = "",
        pv_action_execution_mode: str = "ENABLED",
        action_recipient_mode: str = "DEMO_SINK",
        ses_demo_sink_domain: str | None = None,
    ) -> None:
        self.pv_action_allowlist = pv_action_allowlist
        self.pv_action_execution_mode = pv_action_execution_mode
        self.action_recipient_mode = action_recipient_mode
        self.ses_demo_sink_domain = ses_demo_sink_domain

    @property
    def action_allowlist_addresses(self) -> tuple[str, ...]:
        """Copied from ``provenance_contracts.settings.Settings``, verbatim."""
        return tuple(part.strip() for part in self.pv_action_allowlist.split(",") if part.strip())
