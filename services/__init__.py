"""Deployment-unit namespace.

`services/` holds deployable units, not logical modules. There is exactly one
unit here — `control_plane` — and `implementation/00_IMPLEMENTATION_MAP.md`
section 4.2 forbids adding a second. `ARCHITECTURE.md` section 25, which
specified five services, is superseded and must not be built from.
"""
