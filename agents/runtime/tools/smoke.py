"""``T7.1`` acceptance: print the model id each tier would actually use.

    python -m agents.runtime.tools.smoke --tier E --tier R --print-model-id

    tier=E model=gemini-3.5-flash-lite ok
    tier=R model=gemini-3.7-flash ok

What this proves, and what it deliberately does not
---------------------------------------------------
It proves the **configuration** resolves: the ids are in the allow-set, the
tier mapping is the one canon specifies, and a stale id fails here rather than
on the first artifact. It proves nothing about invocability — and says so, on
every run, until ``ops/gemini-probe.txt`` carries a ``PASS`` line naming the id.

That disclosure is the entire lesson of the previous provider's model-id canon.
``list-foundation-models`` returned ids that were **not invocable**, and every
id frozen from documentation turned out wrong. A smoke tool that printed
``ok`` for a configured-but-unprobed id, with no caveat, is what let that
mistake survive to deployment. There is no API key on this machine, so this
tool makes no network call of any kind; the ``--tier`` lines are configuration,
not capability.

Note that ``ops/gemini-probe.txt`` **already exists** and records ``CANNOT RUN``
— no key, nothing attempted. So the check is on the verdict line, not on the
file: see :func:`probe_verdict`. A transcript is not a result.

``T7.1``'s printed acceptance strings name ``anthropic.claude-*`` ids. They are
superseded by the Gemini pivot recorded in ``PIVOT.md``; the *shape* of the
acceptance — one line per tier, naming the id, and a hard failure on any stale
identifier — is preserved exactly.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from agents.runtime.model_router import (
    ALLOWED_MODEL_IDS,
    ENV_API_KEY,
    PROBE_EVIDENCE_PATH,
    ROUTES,
    GeminiRouterConfig,
    ModelConfigError,
    route,
)
from provenance_domain.enums import ModelTier

__all__ = ["main", "probe_verdict", "read_probe_transcript"]

_TIER_BY_NAME: Final[dict[str, ModelTier]] = {"E": ModelTier.E, "R": ModelTier.R}

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agents.runtime.tools.smoke",
        description=(
            "Resolve and print the configured Gemini model id for each tier. "
            "Makes no model call: there is no API key and no probe transcript."
        ),
    )
    parser.add_argument(
        "--tier",
        action="append",
        choices=sorted(_TIER_BY_NAME),
        dest="tiers",
        help="tier to resolve; repeatable (default: both)",
    )
    parser.add_argument(
        "--print-model-id",
        action="store_true",
        help="accepted for compatibility with the T7.1 acceptance line; the id is always printed",
    )
    return parser


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """Exit 0 when every requested tier resolves, 1 on a configuration failure.

    ``env`` is a parameter rather than an ambient read so that the one
    ``os.environ`` access in this package sits in a CLI entry point where it is
    greppable. ``provenance_contracts.settings`` owns the rule that one module
    reads the environment; when ``Settings`` grows the four ``GEMINI_*`` fields,
    this line is what changes, and nothing else does.
    """
    args = _parser().parse_args(list(argv) if argv is not None else None)
    environment = os.environ if env is None else env
    tiers = [_TIER_BY_NAME[name] for name in (args.tiers or sorted(_TIER_BY_NAME))]

    try:
        config = GeminiRouterConfig.from_env(environment)
    except ModelConfigError as exc:
        print(f"smoke: {exc}", file=sys.stderr)
        return 1

    for tier in tiers:
        model_id = config.model_id_for(tier)
        nodes = ", ".join(sorted(name for name, spec in ROUTES.items() if spec.tier is tier))
        print(f"tier={tier.value} model={model_id} ok")
        print(f"    nodes: {nodes}")
        print(f"    effort: {sorted({route(name).effort or 'none' for name in nodes.split(', ')})}")

    print(f"allow-set: {', '.join(sorted(ALLOWED_MODEL_IDS))}")
    print(
        f"declared capacity fallback (never auto-selected): "
        f"{config.reasoning_fallback_model_id}"
    )

    if config.api_key is None:
        print(f"{ENV_API_KEY} is not set; no call was attempted and none can be.")

    transcript = read_probe_transcript(_REPO_ROOT)
    unprobed = [
        config.model_id_for(tier)
        for tier in tiers
        if probe_verdict(config.model_id_for(tier), transcript) != "PASS"
    ]
    if unprobed:
        print(
            f"PROBE REQUIRED: {PROBE_EVIDENCE_PATH} carries no PASS line for "
            f"{', '.join(unprobed)}. Those ids are documented but unprobed. 'ok' above "
            "means the configuration resolves, not that the model is invocable."
        )
    else:
        print(f"probed: every id above has a PASS line in {PROBE_EVIDENCE_PATH}")
    return 0


def read_probe_transcript(root: Path) -> str:
    """The transcript text, or an empty string when there is none."""
    probe = root / PROBE_EVIDENCE_PATH
    if not probe.is_file():
        return ""
    return probe.read_text(encoding="utf-8", errors="replace")


#: Characters that continue a model id. A match flanked by one of these is a
#: DIFFERENT id that merely contains the one asked about.
_ID_CHAR = "[0-9A-Za-z._-]"


def _names_exactly(model_id: str, line: str) -> bool:
    """True when `line` names `model_id` itself, not an id containing it.

    A bare `model_id in line` reported PASS for a **failing** id whenever a
    longer id containing it passed: `gemini-3.5-flash` is a prefix of
    `gemini-3.5-flash-lite`, the probe invokes both, and both lines sit in the
    same transcript. Whichever appeared first decided the verdict for the
    other.

    That is the identical mistake this module's docstring warns about one level
    up -- reading the file instead of the result -- committed by the function
    that exists to prevent it. `re.escape` because a version number contains
    `.`, which is otherwise a wildcard that would make the collision worse.
    """
    for match in re.finditer(re.escape(model_id), line):
        before = line[match.start() - 1] if match.start() else ""
        after = line[match.end()] if match.end() < len(line) else ""
        if re.fullmatch(_ID_CHAR, before) or re.fullmatch(_ID_CHAR, after):
            continue
        return True
    return False


def probe_verdict(model_id: str, transcript: str) -> str:
    """``PASS``, ``FAIL`` or ``UNPROBED`` for one id.

    **The existence of the file is not the evidence.** ``ops/gemini-probe.txt``
    exists today and records ``CANNOT RUN`` — no key, nothing attempted — so a
    check on ``Path.exists()`` would report every id as probed while nothing
    had ever been invoked. That is the same shape of mistake as reading
    ``list-foundation-models`` and believing the ids it returns are invocable:
    a listing is not an invocation, and a transcript is not a result. The
    verdict is therefore read out of the line that names the id.
    """
    for line in transcript.splitlines():
        if not _names_exactly(model_id, line):
            continue
        if line.lstrip().startswith("PASS"):
            return "PASS"
        if line.lstrip().startswith("FAIL"):
            return "FAIL"
    return "UNPROBED"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
