# Moved — see `ops/PROBE_LEDGER.md`

There is one probe ledger and it lives at **`ops/PROBE_LEDGER.md`**.

This path held a second, script-generated ledger whose PB-1…PB-4 rows read
`NOT RUN` and whose PB-2 cell read `VARIANT: none -- BRUTE_FORCE_PARTITION`,
while the curated ledger one directory up read `PASS` and `VARIANT: A` for the
same probes on the same cluster. Two ledgers under the same name with
contradictory verdicts is worse than one ledger that is wrong, because a
reviewer who finds either one has no reason to look for the other.

`ops/PROBE_LEDGER.md` is the committed path: it is the one `.gitleaks.toml`
enumerates in its transcript allowlist, and `ops/probes/phase0-probe.ps1` now
writes its generated ledger there. Filed as `D-00-005`.

This file is kept as a redirect rather than deleted so that a link or a habit
pointing here lands on the reason rather than on a 404.
