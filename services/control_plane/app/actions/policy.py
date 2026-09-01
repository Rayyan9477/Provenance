"""The recipient allowlist and the execution kill switch. Both default closed.

Authority
---------
- ``packages/python/provenance_contracts/src/provenance_contracts/settings.py``
  -- ``PV_ACTION_ALLOWLIST`` (default ``""``), ``PV_ACTION_EXECUTION_MODE``
  (default ``ENABLED``), ``ACTION_RECIPIENT_MODE`` (default ``DEMO_SINK``),
  ``SES_DEMO_SINK_DOMAIN`` (default ``None``).
- ``docs/specs/15_API_SPEC.md`` section 9.8 step 3 -- the allowlist is the
  counterparty's ``canonical_domain`` plus ``demo-sink.provenance.app``.
- ``docs/quality/23_PHASE_GATES.md`` ``G9.5`` and ``G9.6``.

Empty means nothing, not everything
------------------------------------
``Settings.action_allowlist_addresses`` parses the default ``""`` to ``()``.
This module reads that as **no recipient is permitted**, which is the only
reading that makes the variable a safety control. The alternative reading --
"unset means unrestricted" -- turns a forgotten environment variable into a
message to a real counterparty, and the one operation in this system that
cannot be undone is a message that has already been sent.

Two knobs, deliberately not one
--------------------------------
``ACTION_RECIPIENT_MODE`` decides where a permitted message is *delivered*;
the allowlist decides whether it may be sent *at all*. Collapsing them would
mean flipping ``COUNTERPARTY`` mode silently widened the allowlist. So
:meth:`ActionPolicy.recipient_allowlisted` is asked about the counterparty
address on the intent, and :meth:`ActionPolicy.delivery_address` is asked
afterwards about where the bytes go.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

__all__ = [
    "ACTION_EXECUTION_DISABLED",
    "RECIPIENT_NOT_ALLOWLISTED",
    "ActionPolicy",
]

#: ``G9.5``'s reason code, written to ``action_executions.error_code``.
RECIPIENT_NOT_ALLOWLISTED: Final[str] = "RECIPIENT_NOT_ALLOWLISTED"

#: ``G9.6``'s kill switch, reported when ``PV_ACTION_EXECUTION_MODE=DISABLED``.
ACTION_EXECUTION_DISABLED: Final[str] = "ACTION_EXECUTION_DISABLED"


def _split_address(recipient: str) -> tuple[str, str] | None:
    """``local, domain`` for a well-formed address, else ``None``.

    Deliberately strict. "Parse generously, allowlist strictly" is not a
    trade-off that can be made in both directions at once, and the direction
    that matters here is the one where a malformed string becomes a match.
    """
    local, separator, domain = recipient.partition("@")
    if not separator or not local or not domain or "@" in domain:
        return None
    return local, domain


@dataclass(frozen=True, slots=True)
class ActionPolicy:
    """Which recipients may be written to, whether sending is on, and where to.

    Frozen: a policy that could be mutated after construction is a policy an
    exception handler could widen.
    """

    allowlist: frozenset[str] = field(default_factory=frozenset)
    execution_mode: str = "ENABLED"
    recipient_mode: str = "DEMO_SINK"
    demo_sink_domain: str | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> ActionPolicy:
        """Build from anything carrying the four settings fields.

        Typed as ``Any`` rather than as ``Settings`` on purpose: constructing a
        real ``Settings`` demands a platform, a database URL and a model id,
        none of which has anything to do with whether a recipient is
        allowlisted. Narrowing the dependency to four attributes is what lets
        this be decided in the hermetic lane.
        """
        return cls(
            allowlist=frozenset(
                entry.strip().lower() for entry in settings.action_allowlist_addresses
            ),
            execution_mode=str(settings.pv_action_execution_mode),
            recipient_mode=str(settings.action_recipient_mode),
            demo_sink_domain=settings.ses_demo_sink_domain,
        )

    @property
    def execution_enabled(self) -> bool:
        """``False`` under ``PV_ACTION_EXECUTION_MODE=DISABLED``.

        The documented rollback position for this phase. Approvals continue to
        be recorded; nothing is sent.
        """
        return self.execution_mode == "ENABLED"

    def recipient_allowlisted(self, recipient: str | None) -> bool:
        """May an outbound message be addressed to *recipient*?

        ``None`` is permitted because ``ck_action_intents_recipient`` allows a
        null recipient only for ``INTERNAL_REMINDER``, and an action that
        reaches nobody has nothing for an allowlist to decide.

        An allowlist entry containing ``@`` matches that exact address; an
        entry without one matches every address in that domain. Both forms are
        needed: ``ops/41_RUNBOOK.md`` writes addresses and section 9.8 writes
        domains.
        """
        if recipient is None:
            return True
        parts = _split_address(recipient.strip().lower())
        if parts is None:
            return False
        address = "@".join(parts)
        return address in self.allowlist or parts[1] in self.allowlist

    def delivery_address(self, recipient: str) -> str:
        """Where the bytes actually go, once the allowlist has already said yes.

        Under ``DEMO_SINK`` the local part is carried onto the sink domain, so
        the demo shows a real address shape without reaching a real
        counterparty. With no sink domain configured the address is returned
        unchanged rather than rewritten onto a guess: inventing a domain would
        deliver a real message to a domain nobody owns, which is worse than
        delivering to the address that was explicitly allowlisted.
        """
        if self.recipient_mode != "DEMO_SINK" or not self.demo_sink_domain:
            return recipient
        parts = _split_address(recipient)
        if parts is None:
            return recipient
        return f"{parts[0]}@{self.demo_sink_domain}"
