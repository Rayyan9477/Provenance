# Security policy

## This is hackathon software. It has not been audited for production use.

Provenance was built for the CockroachDB x AWS hackathon, "Build with Agentic
Memory". It is a working system, not a shipped product, and it carries none of
the assurances a product should carry:

- **No security audit, no penetration test, no threat-model sign-off.** The
  controls described in `docs/` are designed and tested, not independently
  reviewed.
- **No compliance posture.** Nothing here is SOC 2, HIPAA, PCI or GDPR
  qualified, and no data-processing agreement exists.
- **No operational security guarantees.** There is no on-call rotation, no
  incident-response process, no key-rotation schedule, and no patch SLA.
- **No supported release.** Only the tip of `main` exists. There are no tags,
  no backports, and no security patches for any earlier commit.

**Do not put real documents into a deployment of this software.** The demo
corpus is entirely synthetic: every counterparty is fictional, every domain is a
`.example` domain, every account reference is invented, and the judge account is
seeded for evaluation only. Real correspondence with a real bank, landlord,
insurer or clinic is exactly the class of data this system is *about*, and
exactly the class of data it is not yet fit to hold.

## Reporting a vulnerability

Report privately. Do not open a public issue with exploit detail, and do not
post it to Devpost.

1. **Preferred:** GitHub private vulnerability reporting at
   <https://github.com/Rayyan9477/Provenance> - the **Security** tab, then
   **Report a vulnerability**. This creates a private advisory thread visible
   only to the maintainers.
2. **If that is unavailable** (the repository is private until submission, and
   private vulnerability reporting may not be reachable to you), open a public
   issue whose entire content is a request for a private channel - no
   reproduction, no payload, no affected path - and a maintainer will open the
   advisory thread and continue there.

Please include, when you have them:

- the commit SHA you tested (`git rev-parse HEAD`), and the deployment if it was
  the hosted demo rather than a local stack;
- a minimal reproduction, ideally as a `curl` sequence or a failing test;
- what an attacker gains: which principal, whose data, and which of the four
  data-integrity invariants or the ownership boundary it crosses;
- any log or trace ids, **scrubbed** - see "Credentials in a report" below.

**Response expectations, stated honestly.** This is a small project with no
on-call. Acknowledgement is best effort within five working days. There is no
fix SLA and there is no bug bounty. If a report is valid and unfixed at
submission time, it will be recorded as carried debt in
`ops/gates/SUBMISSION.md` rather than quietly dropped.

## Credentials in a report, and credentials in this repository

Everything under `ops/` is committed and secret-scanned, including gate logs and
probe transcripts, because the evidence trail is the point. That makes an
accidentally pasted credential a permanent public leak once the repository is
public.

- **Never paste a live connection URL, access key, session token or JWT into an
  issue, an advisory, or a pull request.** Run reproductions so the credential
  never enters the transcript, and pipe pasted output through `tools/scrub.py`.
- **If you find a credential that has already been committed here**, treat it as
  a vulnerability report and send it privately. The response order is fixed:
  **rotate the credential first, rewrite history second.** A history rewrite on
  a public repository does not un-leak anything, and doing it first only removes
  the evidence of what needs rotating.
- `gitleaks detect --source . --exit-code 1` and
  `gitleaks detect --source ops/gates --exit-code 1` are gate assertions G0.3
  and submission item S8, and the scanner configuration is `.gitleaks.toml`. An
  allowlist entry that excuses a whole directory is itself a defect worth
  reporting.

## Scope

Findings in the following are in scope and wanted:

- **Ownership and tenancy boundaries** - any path by which one user's principal
  reads, proposes against, or acts on another user's case, evidence, belief,
  commitment or artifact.
- **The agent read boundary** - the agent role (`pv_agent_reader`) is granted
  `SELECT` on views only and must be refused the base tables. A path that
  reaches a base table, or that returns another user's rows through a view, is
  a finding.
- **The SQL role separation** - `pv_migrator`, `pv_app_reader_writer`,
  `pv_kernel_writer`, `pv_agent_reader`, `pv_ops_reader`. A write reaching the
  database through a role that should not be able to perform it is a finding.
- **Authentication and capability handling** - token verification, principal
  mapping, capability scoping on internal endpoints, idempotency-key handling.
- **Action execution** - any way to cause an outbound action without a recorded,
  human-approved intent, or to replay an approved one.
- **Prompt injection with consequence** - untrusted document text that reaches
  an instruction channel, or that causes a write, an action, or a state change
  that grounding and approval should have prevented. Steering a *ranking* is
  interesting; steering a *commit* is a vulnerability.
- **Secret exposure** - credentials in logs, traces, error messages, gate logs,
  API responses, or the repository itself.

Out of scope:

- The design pack under `docs/`. It is prose; report factual errors as ordinary
  issues.
- The synthetic seed corpus and the seeded judge account, which contain no real
  data by construction.
- Missing production hardening that is already disclosed as absent: rate limits,
  WAF, DDoS protection, account lockout, MFA, session revocation UX, and
  multi-region failover. These are known gaps, not discoveries.
- Anything that requires the maintainer's own AWS credentials, CockroachDB
  Cloud console access, or physical access to the build machine.
- Denial of service against the hosted demo. **Do not load-test, fuzz, or scan
  the demo deployment.** It is a single small environment shared with the
  judges; take it down and you have only broken the demo. Run against a local
  stack instead - `make bootstrap` and the setup in the README get you one.

## Supported versions

| Version | Supported |
|---|---|
| tip of `main` | Yes, best effort |
| any earlier commit or tag | No |

## Licence

This policy covers the software in this repository, which is licensed under the
Apache License 2.0 (`LICENSE`). Section 7 of that licence applies: the work is
provided on an "AS IS" basis, without warranties or conditions of any kind.
