"""Let the Memory Kernel claim its own idempotency keys.

The defect
----------
``commit_trigger_evaluation`` makes its idempotency claim the **first** statement
of the Kernel transaction, deliberately: that is what closes the window in which
the effect commits and the key does not. Against the live cluster, every trigger
evaluation fails::

    POST /internal/v1/triggers/{id}/evaluate -> 500
    psycopg.errors.InsufficientPrivilege: user pv_kernel_writer does not have
    SELECT privilege on relation idempotency_records

That is the demo's prospective-memory step -- one of the four capabilities
``00_PRODUCT.md`` section 2.2 claims ordinary RAG structurally cannot do -- and it
could not fire at all.

Why this is a conflict and not a typo
--------------------------------------
``0008`` revokes it on purpose, with a stated reason::

    # The Kernel can never send anything, and can never mint an approval.
    "REVOKE ALL ON TABLE action_executions, ingest_aliases, idempotency_records,
     processed_events FROM pv_kernel_writer"

Both halves are defensible. ``idempotency_records`` was grouped with the *action*
tables because idempotency was an API-request concern: the app dedupes an inbound
request. The Kernel later took ownership of the trigger path and needs the same
table for a different purpose -- deduping its own evaluation -- which is neither
sending nor approving.

The resolution is the narrowest one that makes the sentence in that comment stay
true:

* ``SELECT`` -- read a prior claim back, so a duplicate returns the stored result
  rather than re-running the effect;
* ``INSERT`` -- make the claim. The Kernel writes the row complete (status,
  response code and body are all in the one statement), so it needs nothing more;
* **no ``UPDATE``, no ``DELETE``** -- the Kernel therefore cannot rewrite or
  remove an idempotency claim made by anything else;
* ``action_intents``, ``action_executions`` and ``ingest_aliases`` stay revoked,
  so the Kernel still cannot send and still cannot mint an approval.

``test_kernel_role_can_reach_its_own_statements.py`` compares the Kernel's
statements against the grant list statically, in both directions, and asserts the
send/approve property is not widened. Nothing caught this until a request reached
the database, because every unit test drives a fake connection -- and a fake
grants everything.

Downgrade is for **local iteration** only. From Phase 13 onward the schema rolls
forward and the code rolls back.
"""

from __future__ import annotations

from alembic import op

revision = "0009b_kernel_idempotency_grant"
down_revision = "0009a_widen_proposal_model_check"
branch_labels = None
depends_on = None

GRANT_DDL = "GRANT SELECT, INSERT ON TABLE idempotency_records TO pv_kernel_writer"

#: Restores `0008`'s position exactly: the Kernel reaches none of it.
REVOKE_DDL = "REVOKE ALL ON TABLE idempotency_records FROM pv_kernel_writer"


def upgrade() -> None:
    op.execute(GRANT_DDL)


def downgrade() -> None:
    """Put the revoke back. **For local iteration only.**

    From Phase 13 onward the schema rolls forward and the code rolls back --
    every revision in this tree records that in its own docstring, because
    nobody should discover the rule during an incident.

    Reverting this makes every trigger evaluation fail again with
    ``InsufficientPrivilege``. That is the correct behaviour for a downgrade:
    it restores the privilege set the revision changed, and the code that
    depends on it belongs to a later revision.
    """
    op.execute(REVOKE_DDL)
