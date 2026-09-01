# Deploying Provenance to Google Cloud Run

Deployment unit 1 (`web`) and unit 2 (`control-plane`) as two Cloud Run
services. This directory is everything needed to build, push and deploy both
images, and to prove afterwards which revision is actually serving.

---

## The short version

```bash
gcloud auth login                                  # once, interactively
echo "PV_GCP_PROJECT=<your-project-id>" > deploy/.env.deploy
deploy/cloudrun.sh up                              # build, push, deploy, print proof
# ... record the video ...
deploy/cloudrun.sh down                            # billing stops
```

**Usually one value, not four.** `cloudrun.sh` falls back to the
repository-root `.env` for the app DSN, the kernel DSN (`PV_DB_KERNEL`, mapped)
and the Google API key. If `.env` already runs the app locally, the project id
is the only thing missing — and it is the one value deliberately *not* read from
`.env`, because it decides which account gets billed and a silently inherited
one is how a deploy lands in the wrong project.

That fallback exists so a live credential never has to be copied between files.
A copy goes via a clipboard; the value goes from `.env` into Secret Manager
without being echoed, pasted or logged.

The one thing that is not a value in a file is the CockroachDB cluster CA
certificate. `up` looks where `ccloud` puts it and stops with instructions if it
is not there -- see *The one file the deploy cannot invent*, below.

`up` is idempotent. Re-running it rebuilds, pushes a new tag, and rolls the
revision forward; it does not rotate signing keys and does not recreate the
registry.

---

## Keeping the cost at nothing

Nothing requires the services to stay up once a deployment has been recorded,
so the sequence is **deploy → prove → switch off** rather than deploy and leave
running. `deploy/cloudrun.sh proof` prints exactly what to capture.

Cloud Run bills per request and per instance-second, and both services are
deployed with `--min-instances 0`, so an idle deployment already costs
essentially nothing. `down` goes further and pins `--max-instances 0` so a
crawler cannot start an instance at all. The images stay in Artifact Registry,
so coming back up needs no rebuild.

Against $150 of credits this deployment is not close to a risk. The thing that
would be — re-embedding 18,035 texts, or an ANN index rebuild — happens on the
CockroachDB cluster and the Gemini API, neither of which is billed here.

Moving the build to Cloud Build does add a line to the bill that the local build
did not have: build time on the machine type `cloudbuild.yaml` asks for
(`E2_HIGHCPU_8`), plus the staging bucket. A cold build is well inside the
half-hour timeout, so a handful of builds is small against $150 -- but it is a
real charge where the old path was free, and no per-minute rate is quoted here
because none has been checked against current pricing.

Do not expect the layer cache to help. `cloudbuild.yaml` runs plain
`gcr.io/cloud-builders/docker` with no `--cache-from`, and each build gets a
fresh worker, so every build is a cold one. Cross-build caching was a property
of the local daemon this path replaced; an earlier draft of this paragraph
carried the claim over, which would have understated the cost.

---

## Why this deployment runs `PV_PLATFORM=local`

This looks wrong at first glance and is deliberate. Read it before changing it.

`PV_PLATFORM` selects an **identity provider and an object store**, not a host.
Cloud Run is the Google Cloud service; the variable is about something else.
The three values resolve like this:

| Value | Identity | Object store |
|---|---|---|
| `aws` | Cognito | S3 |
| `gcp` | Identity Platform | GCS |
| `local` | HS256 via `PV_LOCAL_AUTH_SECRET` | `FilesystemObjectStore` |

`PV_PLATFORM=gcp` would fail this deployment in two places, and the second is
the one that matters:

1. `Settings._platform_requirements` requires `GCS_ARTIFACT_BUCKET`, so the
   process would refuse to start without one.
2. `storage.object_store_for` returns **`UnconfiguredObjectStore`** for `gcp` —
   there is no GCS implementation in this tree. Every artifact upload would
   refuse at runtime, on a process that started cleanly.

`local` is the only mode with a working object store and a working token path,
and it is honest: the bytes really are stored, really addressed by the key
`source_artifacts.s3_key` records, and really read back.

**What it costs.** The filesystem store lives inside the container, so artifact
bytes do not survive a revision replacement and are not shared between
instances. That is why the control plane deploys with `--max-instances 1`. For a
demo whose artifacts are re-seeded from `demo/artifacts/` this is a real
constraint, not a hidden one, and it is written down here rather than discovered
when a second instance serves a 404.

**The right fix, when there is time**, is a `GcsObjectStore` implementing the
existing `ObjectStore` Protocol — one class, because the key helpers are shared
and every caller depends on the Protocol rather than on an implementation. Then
`PV_PLATFORM=gcp` becomes true instead of aspirational, `--max-instances` can
rise, and the durability story gains a managed-storage leg.

---

## Prerequisites

| | |
|---|---|
| `gcloud` authenticated | `gcloud auth login`, then `gcloud config set project <id>` |
| A billing account | The $150 promotion redeems at [console.cloud.google.com/billing/redeem](https://console.cloud.google.com/billing/redeem). **Redeem by 3 September 2026**; credits expire three months after redemption. |
| `python` | Mints the web app's API token and derives the Alembic head that becomes `SCHEMA_REVISION`. |
| The cluster CA certificate | `ccloud` leaves it at `%APPDATA%/postgresql/root.crt` or `~/.postgresql/root.crt`, which is where the script looks; point `PV_CA_CERT_FILE` at it if yours is elsewhere. See *The one file the deploy cannot invent*, below. |

**`docker` is no longer a prerequisite.** The images are built on Cloud Build,
so `gcloud` alone is enough to produce and push them -- which means a judge can
reproduce the image on a machine with no Docker installed at all. A local
`docker` is still used as a fallback if Cloud Build refuses; see *Where the
images are built*, below.

The script enables `run`, `cloudbuild`, `artifactregistry` and `secretmanager`
itself on first run. `cloudbuild` used to be enabled out of caution and is now
load-bearing: without it the primary build path fails and the deploy falls back
to a daemon that may not be there.

---

## What `up` does, in order

1. **Enables four APIs.** Idempotent; a no-op on a second run.
2. **Creates an Artifact Registry docker repository** in the region, if absent.
3. **Writes eight secrets to Secret Manager.** Three are supplied by you (the
   two DSNs and the API key) and are rewritten every run, so editing
   `.env.deploy` and re-running is how a credential is rotated. Four are signing
   keys, minted on first run and **read back unchanged** on every subsequent
   run -- see *Why signing keys are not rotated*, below. The eighth is the
   CockroachDB cluster CA certificate, written from a file on disk.
4. **Grants the runtime service account `secretmanager.secretAccessor`** on
   those eight, and nothing else. A ninth secret, `provenance-api-token`,
   does not exist yet at this point -- it is minted against the deployed control
   plane in step 7 and granted there. See *The accessor grant covers eight
   secrets in the loop, and a ninth elsewhere*, below; the CA was missing from
   that loop until 2026-08-31.
5. **Builds and pushes both images**, on Cloud Build, at
   `--platform linux/amd64`. A local `docker build` is the fallback.
6. **Computes both `.run.app` URLs, then deploys the control plane** with
   `APP_BASE_URL` and `WEB_BASE_URL` already set. They are required settings and
   `Settings` refuses to construct without them, so reading them back from a
   first revision cannot work -- that revision would have to boot first. Cloud
   Run's hostname is deterministic, so they are computed instead, and then read
   back and corrected if Cloud Run minted something else. A computed value that
   is never checked against the real one is the unobserved mapping this project
   keeps filing defects about.
7. **Deploys the web app** pointed at that URL. `PV_API_TOKEN` is minted
   server-side with a seven-day TTL, written to `provenance-api-token`, granted
   to the runtime service account and mounted **by reference** -- never as an
   environment variable, which would put a live bearer token in the revision
   spec. If minting fails, the web app starts in FIXTURE mode and `up` says so
   rather than leaving a judge to discover it.
8. **Prints the proof block.**

### Secrets never appear on a command line

The credential-shaped values are mounted with `--set-secrets`, by reference.
`--set-env-vars` would put them in the revision spec, which is readable by
anyone with `viewer` on the project and is printed in full by
`gcloud run services describe`.

### Why signing keys are not rotated on every deploy

`PROVENANCE_CAPABILITY_HMAC_KEY`, `CURSOR_HMAC_KEY`, `INGEST_ALIAS_HMAC_KEY` and
`PV_LOCAL_AUTH_SECRET` sign capability proofs, pagination cursors, ingest
aliases and local tokens. Minting fresh ones on each deploy would invalidate
every proof and cursor the previous revision issued — which presents as
*intermittent* `403`s, reads as a flaky network, and is precisely the failure
mode `D-08-003` cost a session to find. So `read_or_mint` returns the stored
value whenever there is one.

Rotate deliberately, never incidentally:

```bash
gcloud secrets versions add provenance-capability-hmac-key --data-file=-
```

---

## The one file the deploy cannot invent

CockroachDB Cloud presents a **cluster-specific** CA, not a publicly trusted
one. A laptop has that certificate because `ccloud` wrote it to
`%APPDATA%/postgresql/root.crt` (or `~/.postgresql/root.crt`); a container has
nothing. This cost two revisions to find, and neither error said what was wrong.
With `sslmode=verify-full` and no `sslrootcert`, psycopg looks for
`~/.postgresql/root.crt` and reports *root certificate file … does not exist* --
which reads like a misconfigured path rather than a missing deployment artifact.
The obvious next move, `sslrootcert=system`, is wrong here and fails one step
later with *certificate verify failed*: the system trust store holds public
roots and this cluster is not signed by one of them. That second error is the
more misleading, because it looks like the fix did not take effect.

So the certificate ships, as `provenance-db-ca-cert`. Not because it is secret --
a CA certificate is public by construction -- but because Secret Manager is how
Cloud Run mounts a *file* into a container, and `--set-secrets PATH=NAME:latest`
is the whole mechanism. It lands at `/etc/ssl/cockroach/root.crt`, and both DSNs
are rewritten on the way into Secret Manager so `sslrootcert` names that mount
instead of a path that exists only on the machine running the script.

Downgrading to `sslmode=require` would also make the error go away, and that is
the trap: `require` accepts *any* certificate, so it turns a verified channel to
the database holding the entire corpus into an unverified one, silently, and the
only visible difference is that the error stops.

`up` refuses to deploy without the certificate rather than deploying something
that starts and reports `db_ok: false`. If your copy is not in either default
location, set `PV_CA_CERT_FILE` in `.env.deploy`; the CockroachDB Cloud console
has it under **Connect → CA cert**.

### The accessor grant covers eight secrets in the loop, and a ninth elsewhere

The judge-panel review found `provenance-db-ca-cert` missing from the grant loop
in `cmd_up`, while this runbook described that loop as covering "those seven".
It is now in the loop, which grants eight.

It did not fail on the machine this was built on, and that is the point worth
keeping. The runtime service account there already held the access from an
earlier manual grant, so every deploy from this laptop worked while a deploy
into a fresh project -- a judge reproducing this -- would have produced a
control plane that could not read its own database certificate. Correct on the
author's machine, broken for everyone else, silent in both cases.

A first pass at the mechanical check reported a second missing secret,
`provenance-api-token`, and that was the check's error rather than the script's:
it compared the mounted set against the loop alone, and the token is granted at
its mint site further down, because it is signed against the deployed control
plane and does not exist when the loop runs. The check now looks for a grant
anywhere in the script. Recorded here because a tightened rule that produces a
confident false positive is worth exactly as much scepticism as the loose one
that produced a false negative.

An earlier draft of this section explained the CA's severity by saying a
file-mounted secret makes `gcloud run deploy` refuse outright. That is not true
and the repository disproves it: commit `775b47d` deployed and served with
`db_ok=True` while the loop already omitted the CA and the control plane already
mounted it. Cloud Run resolves environment and file secret references at the
same point. The omission needed no dramatic mechanism to be worth fixing.

`tools/tests/test_deploy_secret_grants.py` now compares the mounted set with the
granted set in both directions, so the two cannot drift apart again.

The correct set for that loop is these eight:

```
provenance-db-app-url            provenance-capability-hmac-key
provenance-db-kernel-url         provenance-cursor-hmac-key
provenance-db-ca-cert            provenance-ingest-alias-hmac-key
provenance-google-api-key        provenance-local-auth-secret
```

`provenance-api-token` is the ninth secret and is granted separately, in the
step that mints it. That is not an oversight: the token is signed against the
deployed control plane, so it does not exist when the loop runs.

The loop warns rather than fails on a binding that is already present, so a
`CANNOT` line during `up` is not evidence of a missing grant. Ask the policy
instead of trusting either the script or this list:

```bash
SA="$(gcloud projects describe "$PV_GCP_PROJECT" \
        --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
for s in provenance-db-app-url provenance-db-kernel-url provenance-db-ca-cert \
         provenance-google-api-key provenance-capability-hmac-key \
         provenance-cursor-hmac-key provenance-ingest-alias-hmac-key \
         provenance-local-auth-secret provenance-api-token; do
  gcloud secrets get-iam-policy "$s" --format='value(bindings.members)' 2>/dev/null \
    | grep -q "$SA" && echo "ok      $s" || echo "MISSING $s"
done
```

---

## Where the images are built, and why it is not your laptop

**Cloud Build first, a local `docker build` as the fallback.** It used to be the
other way round, and the reason it changed is worth a paragraph.

Building locally made the entire deployment path depend on a working Docker
daemon on one particular Windows machine. That daemon died twice mid-deploy --
`Docker Desktop is unable to start`, then a 500 from the engine pipe -- and each
time it took the only route to production with it. A deploy that needs one
laptop's Docker Desktop is that laptop's deploy, not the project's. Cloud Build
needs only `gcloud`, which is also the better artifact to ship: anyone who
clones this repository can reproduce both images without installing Docker.

The original objection to Cloud Build was real and is now answered rather than
ignored. `gcloud builds submit --tag` assumes a Dockerfile at the root of the
build context, and both Dockerfiles here live under `deploy/` while needing the
repository root as context -- the API image needs `packages/python/` and
`agents/` as well as `services/`. That is why `deploy/cloudbuild.yaml` exists:
one config, parameterised by `_IMAGE`, `_DOCKERFILE` and `_GIT_SHA`, so the two
images still share a single code path instead of needing a throwaway config
each. It passes `--platform=linux/amd64` for the same reason the local build
did; Cloud Build's workers are amd64 already, so there it is an assertion rather
than a cross-build.

The fallback is not silent. If `gcloud builds submit` fails for any reason -- the
API not enabled on a fresh project, a missing permission, a quota -- the script
prints a `[ CANNOT ]` line reading *Cloud Build refused; falling back to a local
docker build*, then tries `docker build --platform linux/amd64` and a push. If
`docker` is not on `PATH`, or that build fails too, the deploy stops and says
both routes failed. It never reports success for an image it did not produce.

**`.gcloudignore` governs what gets uploaded, not `.dockerignore`.** `gcloud`
does not read `.dockerignore` at all; without a `.gcloudignore` it synthesises
an ignore set from `.gitignore`, which does not exclude
`db/seeds/vectors.parquet` -- 69.9 MB, tracked, and read by no image. Now that
every build ships a context to Google, that file is load-bearing for secrecy as
well as for size: what it fails to exclude lands in Cloud Build's staging
bucket, which is a real place with real retention.

**Keep `.dockerignore` and `.gcloudignore` in step.** They are two explicit
lists rather than one including the other, because gcloud parses Docker's `!`
re-inclusion syntax slightly differently, and a difference in interpretation
there is a credential in a bucket.

---

## Verifying a deployment

```bash
CP=$(gcloud run services describe provenance-control-plane \
       --region us-east4 --format='value(status.url)')

curl -s  "$CP/v1/version" | python -m json.tool
curl -s -o /dev/null -w '%{http_code}\n' "$CP/v1/healthz"   # 200
curl -s -o /dev/null -w '%{http_code}\n' "$CP/v1/cases"     # 401
```

**Read `db_ok`, not the status code.** Startup deliberately survives a refused
connection pool and reports `db_ok: false` on `/v1/version` rather than
crash-looping before it can say anything — which is the right behaviour on a
platform that would otherwise show you a retry count and no reason.
`/v1/healthz` is a bare liveness probe and never carries `fixture_mode`.

`GET /v1/version` is unauthenticated on purpose, so a judge can `curl` it. It is
the single authoritative disclosure channel: `git_sha`, `fixture_mode`,
`agent_mode`, `otlp_export`, `schema_revision`, `db_ok`.

---

## The proof the video needs

Any one of these satisfies "visual proof of Google Cloud deployment". Showing
two costs ten seconds and removes the argument.

1. **The Cloud Run dashboard** with both services listed and the region visible.
2. **`curl $CP/v1/version` on camera**, at the `.run.app` host, showing
   `fixture_mode: false` and `db_ok: true`.
3. **The live app** at the web service's URL with no fixture banner.

`fixture_mode: true` in a recorded demo invalidates the submission under the
project's own rules. Check it before recording, not after.

---

## Failure modes worth knowing before 2am

**`exec format error` on start.** The image was built for arm64. Both build
paths pass `--platform linux/amd64`, the Cloud Build one in `cloudbuild.yaml`
and the fallback one on the `docker build` line; if you build by hand, pass it
too.

**`up` says Cloud Build refused and falls back to a local build.** The warning
is the only place that failure is visible, because the script discards
`gcloud builds submit` output. Re-run the submit by hand to read the reason:

```bash
gcloud builds submit --config deploy/cloudbuild.yaml \
  --substitutions=_IMAGE=<image ref>,_DOCKERFILE=deploy/Dockerfile.web,_GIT_SHA=$(git rev-parse HEAD) .
```

On a fresh project it is usually `cloudbuild.googleapis.com` still propagating
after `up` enabled it, in which case a second `up` succeeds. It can also be the
Cloud Build service account lacking `artifactregistry.writer` on the repository.

**The container starts and every read is a `500`.** Look at `db_ok` first. A
CockroachDB Cloud cluster restricts by IP allowlist, and Cloud Run's egress is
not a fixed address. Either allow `0.0.0.0/0` on the cluster for the duration of
the demo (it is password-plus-TLS protected, but say so out loud), or attach a
VPC connector with Cloud NAT and allowlist the NAT address. Read the log before
assuming the allowlist: *certificate verify failed* is the CA, not the network,
and *The one file the deploy cannot invent* above is the whole story.

**The web app shows a wall of error states instead of a fixture banner.**
`PV_API_BASE_URL` is set and `PV_API_TOKEN` is not. Every read runs in a server
component and the control plane correctly answers `401` to an anonymous read.
LIVE mode needs both variables; there is no partial mode.

**`gcloud run deploy` succeeds and the URL 404s.** The service deployed but the
container never bound. Next.js standalone defaults to `localhost`; the image
sets `HOSTNAME=0.0.0.0` for exactly this reason. Check it survived any edit.

**Secret access denied, at deploy or at start.** The runtime service account
binding is per-secret, so adding a secret means adding a binding; the loop in
`cmd_up` covers the eight listed above and warns rather than failing on a
binding that is already present. A missing binding shows up as the container
starting and reporting `db_ok: false` -- including for the CA, which is mounted
as a file: there is no earlier, louder failure for file-mounted secrets, and an
earlier draft of this runbook claimed there was. Run the policy check in *The
accessor grant covers eight secrets in the loop, and a ninth elsewhere* before
assuming the DSN is wrong.

---

## Files here

| File | What it is |
|---|---|
| `Dockerfile.control-plane` | Deployment unit 2. Two-stage; venv built in the first, no build chain in the second. |
| `Dockerfile.web` | Deployment unit 1. Requires `output: "standalone"` in `next.config.mjs`. |
| `cloudrun.sh` | `up` / `proof` / `down` / `destroy`. |
| `cloudbuild.yaml` | One Cloud Build config for both images, parameterised by `_IMAGE`, `_DOCKERFILE` and `_GIT_SHA`. This is the primary build path. |
| `.env.deploy.example` | Template. The real file is gitignored by `**/.env.*`. |

`.gcloudignore` and `.dockerignore` live at the repository root, not here,
because both are read relative to the build context -- which is the root, for
both images.

`workers/` is not deployed. It holds four empty `__init__.py` files; the outbox
dispatcher actually lives in `services/control_plane/app/events/`.
