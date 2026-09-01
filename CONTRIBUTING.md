# Contributing

Thanks for looking at Provenance. This file covers what you need to get a
working tree, and the few rules that are load-bearing rather than stylistic.

---

## Getting set up

You need **Python 3.12** (`>=3.12,<3.13` — the pin is deliberate), **Node 20+**,
and Docker if you want a local database.

```bash
make bootstrap          # the four provenance_* packages plus the dev toolchain
cp .env.example .env    # fill in what you need; it is annotated by section
make test-fast          # L1 only: hermetic, no database, no network
```

`make help` prints every target with a one-line description. Nothing in this
repository needs a cloud account to develop against — `make run-crdb` gives you
a local single-node CockroachDB, and `make run-sink` a local SMTP sink.

---

## The test lanes

Tests are layered, and the layer decides what it is allowed to touch.

| Lane | Command | Needs |
|---|---|---|
| **L1** | `make test-fast` | nothing — hermetic |
| **L2** | `make test-db` | a CockroachDB cluster (`PROVENANCE_TEST_DB_URL`) |
| **all** | `make test-all` | a cluster and Gemini credentials |
| **commit lane** | `make test` | what CI runs on every push |

Run `make lint` before you push: ruff, `mypy --strict`, and import-linter
contracts. All three must be clean; there is no "warnings are fine" tier.

---

## The rules that are actually enforced

Some of this project's invariants are checked by custom linters rather than by
review, because review does not scale and memory does not survive a refactor.
If you trip one of these, the linter is right and the code is wrong.

- **`write_path_lint`** — only the Memory Kernel may issue canonical writes.
  Agents propose; they hold no write credential. A canonical `INSERT`/`UPDATE`
  appearing under `agents/`, `workers/`, `apps/web/` or `packages/` fails the
  build.
- **`contract_lint`** — module dependency direction. Layers may not import
  upward.
- **`txn_purity_lint`** — no model call, no network call, no clock read inside
  a kernel transaction. The write path is deterministic and stays that way.
- **`invariant_map_check`** — every declared invariant maps to a test that
  would fail if it were violated. An unmapped invariant is reported as
  `UNPROVEN` rather than assumed.
- **`check-render-honesty.mjs`** — six rules over the web app (see below).

---

## The honesty doctrine

This is the part worth reading even if you only ever send one patch. The
project's central claim is that its output can be trusted, and that claim is
cheap to destroy. Three rules protect it:

**Absence is marked, never implied.** A read path with nothing behind it must
say so. Returning `[]` from an unimplemented method is the failure mode this
codebase is organised against: an empty list is indistinguishable from a real
empty result, and it is believable enough that nobody investigates. Unbuilt
capability answers **`501 NOT_IMPLEMENTED`** and names the subsystem it is
waiting on. In the UI, missing values render through the `<Absent>` primitive —
never as an em dash, a blank, or a zero.

**`CANNOT RUN` is not `FAIL`, and neither is `PASS`.** Evaluation and rehearsal
transcripts distinguish a check that failed from a check that could not be
attempted. Collapsing the two directions is how a green log gets produced for
work that never happened.

**No hardcoded verdicts.** A `Verdict.PASS` that is not computed from a
measurement is worse than no verdict at all, because it sits beside computed
facts and borrows their credibility. If you cannot measure it, report that you
cannot measure it.

The same instinct applies to prose. Don't write a number into a document unless
something produced it, and say where it came from.

---

## Commits and pull requests

- Branch from `main`. Keep the change focused.
- Make `make lint` and `make test` pass before you open the PR.
- If you change behaviour, change or add the test that proves it. If you fix a
  bug, the test should fail before your fix and pass after — say so in the PR.
- If you touch a documented invariant, update the document in the same commit.
  Documentation that lags the code is how the invariants stop being true.
- Never commit a credential, a connection string, or a live hostname. `.env`
  and `.env.*` are gitignored, gitleaks runs in CI over the full history, and
  `tools/scrub.py` exists to clean transcripts before they are committed.

---

## Reporting bugs

Open an issue with what you ran, what you expected, and what happened. If it
involves the database or a deployed service, include the output of
`GET /v1/version` — it carries the git sha, the schema revision, and whether
the process is in fixture mode, which answers most of the first round of
questions.

Security issues should not go in the issue tracker. See
[`SECURITY.md`](SECURITY.md).

---

## Licence

By contributing you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE), the same terms that cover the project.
