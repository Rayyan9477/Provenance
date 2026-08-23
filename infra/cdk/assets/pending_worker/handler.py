"""Placeholder bundle for a Lambda worker whose handler has not been written.

``provenance_infra.workers.resolve_code`` uses this directory only when
``workers/<module>/handler.py`` is absent from the working tree. Phases 8
through 10 write the real handlers; until then a function built from this
bundle fails loudly on the first invocation rather than returning a plausible
success.

It is never a fallback at runtime: if a deployed function reaches this code, the
deploy shipped an unwritten worker and that is the bug.
"""

from __future__ import annotations

from typing import Any

MESSAGE = (
    "provenance worker handler not implemented: this Lambda was bundled from "
    "infra/cdk/assets/pending_worker because workers/<module>/handler.py did not "
    "exist at synth time. Build the worker, then re-synthesise."
)


def handler(event: Any, context: Any) -> dict[str, str]:
    raise NotImplementedError(MESSAGE)
