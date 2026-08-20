"""Provenance build and gate tooling.

This package is a package so that `python -m tools.<name>` works from the
repository root, which is the invocation form the `Makefile` and
`EXECUTION/72_DEFECT_PROTOCOL.md` section 11.3 both use. Nothing here ships in a
deployment unit; `implementation/00_IMPLEMENTATION_MAP.md` section 5 places
`tools/` outside the four units (`web`, `control-plane`, `agent-runtime`,
`workers`).

Modules, and the document that makes each one binding:

    scrub.py           quality/23_PHASE_GATES.md section 2.2 - redaction before a
                       gate log is committed and gitleaks-scanned.
    defect_lint.py     EXECUTION/72_DEFECT_PROTOCOL.md sections 5.2, 11.2.
    close_proof.py     EXECUTION/72_DEFECT_PROTOCOL.md section 7.4.
    sabotage_guard.py  EXECUTION/72_DEFECT_PROTOCOL.md section 10.2.

`gate.sh` is a shell script rather than a module on purpose: it wraps an
arbitrary child process and must propagate that child's exit status, and a
Python wrapper adds an interpreter between the assertion and its status for no
gain.
"""
