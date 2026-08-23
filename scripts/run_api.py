"""Serve the control plane on an event loop psycopg will actually accept.

Why this file exists
--------------------
``make run-api`` used to invoke ``uvicorn --loop asyncio`` directly, and the
recipe's own comment claimed that flag was what made Windows work:

    On Windows: psycopg async refuses uvicorn's default proactor loop.
    ``--loop asyncio`` is not optional there.

The first half is true and the second half does not follow. Measured against
uvicorn 0.40.0, ``--loop asyncio`` resolves to::

    def asyncio_loop_factory(use_subprocess: bool = False):
        if sys.platform == "win32" and not use_subprocess:
            return asyncio.ProactorEventLoop
        return asyncio.SelectorEventLoop

so on Windows the flag selects **the proactor loop** -- precisely the one
psycopg refuses. The observable result was the server starting cleanly,
answering ``GET /v1/version`` with ``200``, and reporting ``db_ok=false`` while
the log filled with ``Psycopg cannot use the 'ProactorEventLoop'``. Every
request that needed canonical memory failed, and the health endpoint said so
honestly -- which is the only reason it was noticeable at all.

That the loop was the *whole* problem is measured, not assumed: run under a
selector loop the same DSN connects as ``pv_kernel_writer`` to ``provenance``,
CockroachDB v26.2.5, and counts 18,035 rows in ``evidence_items`` -- the
corpus size ``CANONICAL_DECISIONS.md`` records. Nothing about the DSN, the TLS
material or the role grants was ever wrong.

Setting a policy would not fix it. uvicorn 0.40 passes a *loop factory* to
``asyncio.run`` rather than consulting the event loop policy, so
``WindowsSelectorEventLoopPolicy`` is simply ignored. The factory is the only
lever, and the CLI does not expose it -- hence a runner rather than a flag.

On Linux and macOS ``asyncio_loop_factory`` already returns
``SelectorEventLoop``, so this module changes nothing there. It is written to
be correct on every platform rather than to branch on one.
"""

from __future__ import annotations

import asyncio
import selectors
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


def _loop_factory() -> asyncio.AbstractEventLoop:
    """A selector loop, explicitly, on every platform.

    ``SelectSelector`` rather than the default is deliberate: on Windows the
    default selector for ``SelectorEventLoop`` is already ``SelectSelector``,
    and naming it means this does not silently change if that default does.
    """
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


def main(argv: list[str] | None = None) -> int:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    config = uvicorn.Config(
        "services.control_plane.app.main:build_app",
        factory=True,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        # Not `loop="asyncio"`: that is the flag whose Windows resolution is the
        # defect. The loop is supplied below instead.
        loop="none",
    )
    server = uvicorn.Server(config)
    asyncio.run(server.serve(), loop_factory=_loop_factory)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
