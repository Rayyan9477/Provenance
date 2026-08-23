"""The one embedding-input template, as the seed needs it (``T2.8``).

Authority
---------
- ``docs/specs/13_RETRIEVAL_SPEC.md`` section 12.1 -- the template, its nine
  rules, and ``EMBEDDING_TEMPLATE_VERSION``.
- ``docs/CANONICAL_DECISIONS.md`` -> Bedrock model id canon --
  ``amazon.titan-embed-text-v2:0``, 1024 dimensions, frozen
  ``EMBEDDING_VERSION`` ``v1``.

Why this lives in ``scripts/seed`` and not in ``provenance_domain``
-------------------------------------------------------------------
Section 12.1 places the shipped implementation at
``provenance_domain/retrieval/embedding.py``, which is authored in Phase 6
(``T6.1``). The seed needs the identical bytes four phases earlier, and the two
must not diverge: **the stored vectors and the query vectors have to come out of
the same template or they live in different neighbourhoods of the same space.**

The resolution is a copy that is *checked* rather than trusted. When
``provenance_domain.retrieval.embedding`` appears, :func:`build_embedding_text`
here must be deleted and re-exported from it -- and until then,
``docs/specs/13_RETRIEVAL_SPEC.md`` section 12.1 is transcribed verbatim below,
including ``EMBEDDING_TEMPLATE_VERSION``, so a diff against the spec is a
three-second read.

A note on rule 8
----------------
Section 12.1 rule 8 says "``embedding_text`` is stored verbatim on
``evidence_items``". There is no such column: migration ``0002`` gives the table
``normalized_text`` and ``normalized_text_sha256`` and nothing else. The seed
therefore stores the sha256 of ``normalized_text`` in the column that exists,
and keys its own on-disk vector cache by the sha256 of the *template render*,
which is the value that actually determines the vector. Filed as a spec
discrepancy rather than resolved silently.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime
from decimal import Decimal

__all__ = [
    "EMBEDDING_MODEL_ID",
    "EMBEDDING_TEMPLATE_VERSION",
    "EMBEDDING_VERSION",
    "MAX_BODY_CHARS",
    "build_embedding_text",
    "embedding_text_sha256",
]

EMBEDDING_TEMPLATE_VERSION = "tmpl1"
EMBEDDING_VERSION = "v1"

#: Bare model id. Third-party and Amazon models take bare ids on Bedrock; only
#: Anthropic chat models take a ``us.`` inference-profile prefix. The two rules
#: are mirror images -- ``CANONICAL_DECISIONS.md`` -> Bedrock model id canon.
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"

MAX_BODY_CHARS = 900

_WS = re.compile(r"\s+")

#: U+00A0. NFKC leaves it alone, so the template folds it explicitly.
_NBSP = chr(0xA0)


def _clean(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    # NFKC leaves U+00A0 alone; 13_RETRIEVAL_SPEC.md section 12.1 folds it to a
    # plain space so a mail client's non-breaking space cannot move the vector.
    s = s.replace(_NBSP, " ")
    return _WS.sub(" ", s).strip()


def build_embedding_text(
    *,
    evidence_type: str,
    counterparty_name: str | None,
    predicate: str | None,
    valid_from: datetime | None,
    valid_to: datetime | None,
    currency: str | None,
    amount: Decimal | None,
    has_identifier: bool,
    normalized_text: str,
) -> str:
    """Render the six fixed header lines plus a capped body.

    Field order is fixed and total: absent fields render as ``unknown`` /
    ``none`` / ``false`` and are never omitted, because a missing line shifts
    every downstream token and moves the vector for a reason that has nothing
    to do with meaning.
    """
    if valid_from or valid_to:
        vf = valid_from.date().isoformat() if valid_from else "open"
        vt = valid_to.date().isoformat() if valid_to else "open"
        valid = f"{vf}/{vt}"
    else:
        valid = "unknown"

    money = f"{currency} {amount:.2f}" if currency and amount is not None else "none"
    body = _clean(normalized_text)[:MAX_BODY_CHARS]

    return (
        f"[type={evidence_type}]\n"
        f"[counterparty={_clean(counterparty_name) if counterparty_name else 'unknown'}]\n"
        f"[predicate={predicate or 'unknown'}]\n"
        f"[valid={valid}]\n"
        f"[money={money}]\n"
        f"[has_identifier={'true' if has_identifier else 'false'}]\n"
        f"{body}"
    )


def embedding_text_sha256(text: str) -> bytes:
    """The cache key. Change the template and every key changes with it."""
    return hashlib.sha256(text.encode("utf-8")).digest()
