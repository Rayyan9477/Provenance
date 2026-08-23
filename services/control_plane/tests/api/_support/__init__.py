"""Hermetic support for the Phase 8 API suites.

Nothing here reaches a network, a database, or a credential store. The RSA
key used to sign test tokens is minted in-process at fixture time
(:mod:`_support.rsa`) precisely so that no key material is ever committed:
``G0.3`` scans this repository for credential-shaped literals and a checked-in
PEM would be one.
"""

from __future__ import annotations

__all__ = ["fakes", "rsa", "tokens"]
