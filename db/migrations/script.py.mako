"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}

Hand-written CockroachDB DDL. Rules that apply to every revision in this chain
(``specs/10_DATABASE_DDL.md`` section 16):

1. **No revision mixes DDL and DML.** CockroachDB rejects a schema change that
   follows a data write in the same transaction. The seed is a separate program.
2. Literal SQL through ``op.execute()``, never ``op.create_table()``:
   SQLAlchemy's dialect emits none of ``VECTOR``, ``FAMILY``, ``STORING`` or
   partial indexes.
3. ``downgrade()`` is always implemented, in reverse creation order. Downgrade
   is for **local iteration only**; from Phase 13 onward schema rolls forward
   and code rolls back.
"""

from __future__ import annotations

from alembic import op

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = None
depends_on = None


def upgrade() -> None:
    ${upgrades if upgrades else "raise NotImplementedError"}


def downgrade() -> None:
    ${downgrades if downgrades else "raise NotImplementedError"}
