# `docs/diagrams`

Diagram sources for Provenance. Mermaid, kept as text in the repository so that
a diagram drifting from the code is a reviewable diff rather than a stale PNG
nobody opened.

## Contents

| File | What it shows |
|---|---|
| [`architecture.md`](architecture.md) | The architecture diagram. Five mermaid diagrams: the system and its single canonical write path; the six-way data spine; the SQL role lattice; the human-approval gate; and the deployment picture. |

## How to render

**GitHub** renders ```` ```mermaid ```` blocks inline. Opening
[`architecture.md`](architecture.md) in the web UI is the intended path, and it
is what most readers will do.

**Locally**, any of:

```bash
# static SVG/PNG, one file per diagram
npx -y @mermaid-js/mermaid-cli -i docs/diagrams/architecture.md -o architecture.svg

# VS Code
#   extension: "Markdown Preview Mermaid Support"

# paste a single block into https://mermaid.live
```

**To check the source parses** without rendering it — which is what was done
before committing — call `mermaid.parse()` on every fenced block under a DOM
shim (`jsdom` plus a `CSSStyleSheet` stand-in). A parse failure in a diagram is
invisible on GitHub: the block silently renders as an error box, so it is worth
checking rather than assuming.

## Conventions these diagrams follow

**Three build states, never blurred.** Every component carries one of `BUILT`,
`IN FLIGHT` or `NOT BUILT`, and the meaning is fixed:

| Badge | Meaning |
|---|---|
| `BUILT` | Exists in this tree, has tests, and the tests run. |
| `IN FLIGHT` | Partly in the tree. The named gaps are listed under the diagram. |
| `NOT BUILT` | Does not exist. Drawn only where omitting it would misrepresent the shape of the system. |

A diagram that claims unbuilt capability is exactly the dishonesty this
project's gate system exists to prevent, so the badges are part of the diagram
rather than a caveat added underneath it. If you extend these diagrams, badge
every node you add.

**Edge styles carry meaning.** In the system diagram:

| Style | Meaning |
|---|---|
| thick (`==>`) | A canonical write. There is exactly **one** into the database, and that is the point of the diagram. |
| solid (`-->`) | A proposal or a request. |
| dotted (`-.->`) | A read, or an advisory call. |
| dotted with a cross (`-.-x`) | A **refusal** — a path that does not exist because no grant backs it. Not an omission. |

**Colour is redundant, never load-bearing.** Every node states its status in
text as well as its fill, so the diagrams survive greyscale printing, dark mode
and colour-vision differences.

**Fills are light with dark text**, deliberately, so the diagrams stay legible
under both GitHub themes.

## Rules for changing these files

1. **Do not badge something `BUILT` you have not run.** The claim must be
   checkable from the tree, and the check should be named in the table beneath
   the diagram.
2. **Never put a credential, DSN, password, API key, hostname or cluster id in a
   diagram.** This repository is public. Filter any file you touch through the
   project scrubber before committing:
   ```bash
   python -m tools.scrub docs/diagrams/architecture.md | diff - docs/diagrams/architecture.md
   ```
   No output means nothing of a known secret shape was found. That is a filter,
   not a proof: `gitleaks detect` is the second one, and neither catches a
   credential that looks like ordinary prose.
3. **`docs/CANONICAL_DECISIONS.md` outranks these diagrams.** If they disagree,
   the register is right and the diagram is a defect.
4. **Re-check the mermaid parses** after editing. A semicolon inside a
   `sequenceDiagram` message is a statement separator, not punctuation — that
   one has already bitten once.
