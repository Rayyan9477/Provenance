#!/usr/bin/env bash
#
# Deploy Provenance to Google Cloud Run. Idempotent: safe to re-run.
#
#     deploy/cloudrun.sh up        build, push, deploy both services, print proof
#     deploy/cloudrun.sh proof     re-print the proof commands for a live deploy
#     deploy/cloudrun.sh down      scale both services to zero instances
#     deploy/cloudrun.sh destroy   delete the services and the image repository
#
# Why `down` exists and why you should use it
# -------------------------------------------
# The credit email is explicit: the project does not need to be live while
# judges review it — the rules ask only for *proof* it was built and deployed on
# Google Cloud, which the demo video carries. Cloud Run already scales to zero
# on idle, so `up` costs almost nothing while nobody is looking at it. `down`
# is the belt to that braces: it pins max-instances to 0 so an accidental crawl
# cannot start an instance at all. Run `proof` first, record the video, then
# `down`.
#
# Configuration
# -------------
# Reads deploy/.env.deploy if present, else the environment. Required:
#
#   PV_GCP_PROJECT            the project id (not the number, not the name)
#   COCKROACH_DATABASE_URL    app-role DSN, sslmode=verify-full
#   COCKROACH_KERNEL_URL      pv_kernel_writer DSN
#   GOOGLE_API_KEY            AI Studio key
#
# Optional, with the defaults shown:
#
#   PV_GCP_REGION=us-east4    CANONICAL_DECISIONS.md: same physical metro as the
#                             CockroachDB cluster on AWS us-east-1, so the
#                             cross-cloud hop stays single-digit milliseconds
#                             instead of 70+.
#   PV_GCP_REPO=provenance    Artifact Registry repository name.
#
# Secrets never appear in a command line. The four credential-shaped values go
# into Secret Manager and are mounted by reference; `gcloud run deploy
# --set-env-vars` would put them in the revision spec, which is world-readable
# to anyone with viewer on the project and is printed by `gcloud run services
# describe`.

set -euo pipefail

# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"

# Two sources, in this order: deploy/.env.deploy wins, and the repository-root
# .env fills whatever it did not set.
#
# Why fall back to .env at all
# ----------------------------
# Three of the four required values already live there — the app DSN, the kernel
# DSN (under its own name, see the mapping below) and the Google API key. Asking
# a human to copy a live credential from one file into another is asking them to
# put it on a clipboard, and this project has already paid for a credential that
# travelled through a channel it should not have. Reading it in place means the
# secret goes from .env straight into Secret Manager without ever being echoed,
# pasted or logged.
#
# .env is sourced in a SUBSHELL and only the four names below are exported back.
# Sourcing it wholesale would import forty variables into this script's
# environment — including APP_BASE_URL and WEB_BASE_URL, which `up` computes
# from the deployed service and must not inherit from a laptop's settings.
if [[ -f "${HERE}/.env.deploy" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "${HERE}/.env.deploy"
  set +a
fi

if [[ -f "${ROOT}/.env" ]]; then
  # `PV_DB_KERNEL` is the seed's and the runbook's name for the pv_kernel_writer
  # DSN; `COCKROACH_KERNEL_URL` is Settings' name for the same value. Two
  # registries for one fact is a defect this repository has filed twice
  # (the kernel pool silently failed to open while the app pool worked), so the
  # mapping is made explicit here rather than left to whichever name is set.
  _from_dotenv() {
    ( set -a; . "${ROOT}/.env" >/dev/null 2>&1; set +a; printf '%s' "${!1:-}" )
  }
  : "${COCKROACH_DATABASE_URL:=$(_from_dotenv COCKROACH_DATABASE_URL)}"
  : "${COCKROACH_KERNEL_URL:=$(_from_dotenv COCKROACH_KERNEL_URL)}"
  : "${COCKROACH_KERNEL_URL:=$(_from_dotenv PV_DB_KERNEL)}"
  : "${GOOGLE_API_KEY:=$(_from_dotenv GOOGLE_API_KEY)}"
  : "${GOOGLE_API_KEY:=$(_from_dotenv GEMINI_API_KEY)}"
  # The migrator DSN is lifted for one purpose and never leaves this machine:
  # reading `alembic_version` so `schema_revision` on /v1/version is measured
  # rather than guessed. It is deliberately NOT mounted into either revision --
  # the control plane runs as pv_app_reader_writer, which has no SELECT on that
  # table by design, and handing a serving container the migrator role to answer
  # a disclosure question would be a much worse trade than reporting the field
  # absent. Without this line SCHEMA_REVISION resolved empty and the first
  # deploy after the fix still served `schema_revision: null`.
  : "${PV_DB_MIGRATOR:=$(_from_dotenv PV_DB_MIGRATOR)}"
  export COCKROACH_DATABASE_URL COCKROACH_KERNEL_URL GOOGLE_API_KEY PV_DB_MIGRATOR
fi

# The project id is NOT read from .env. It is not a secret, it is the one thing
# that decides which account gets billed, and inheriting it silently from a file
# nobody re-reads is how a deploy lands in the wrong project.
PROJECT="${PV_GCP_PROJECT:-}"
REGION="${PV_GCP_REGION:-us-east4}"
REPO="${PV_GCP_REPO:-provenance}"
GIT_SHA="$(cd "${ROOT}" && git rev-parse HEAD 2>/dev/null || echo unset)"

TAG="${GIT_SHA:0:12}"

REGISTRY="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}"
CP_IMAGE="${REGISTRY}/control-plane:${TAG}"
WEB_IMAGE="${REGISTRY}/web:${TAG}"

CP_SERVICE="provenance-control-plane"
WEB_SERVICE="provenance-web"

# ---------------------------------------------------------------------------
# output helpers. Verdicts are three-valued here for the same reason they are
# everywhere else in this repository: "could not check" and "checked and it
# failed" lead to opposite decisions.
# ---------------------------------------------------------------------------

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '  [ OK        ] %s\n' "$*"; }
info() { printf '  [ ..        ] %s\n' "$*"; }
warn() { printf '  [ CANNOT    ] %s\n' "$*" >&2; }
die()  { printf '\n  [ FAILED    ] %s\n\n' "$*" >&2; exit 1; }

require_config() {
  local missing=()
  [[ -n "${PROJECT}" ]]                        || missing+=("PV_GCP_PROJECT")
  [[ -n "${COCKROACH_DATABASE_URL:-}" ]]       || missing+=("COCKROACH_DATABASE_URL")
  [[ -n "${COCKROACH_KERNEL_URL:-}" ]]         || missing+=("COCKROACH_KERNEL_URL")
  [[ -n "${GOOGLE_API_KEY:-}" ]]               || missing+=("GOOGLE_API_KEY")
  if (( ${#missing[@]} )); then
    die "unset: ${missing[*]}
  Put them in deploy/.env.deploy (gitignored) or export them.
  deploy/.env.deploy.example is the template."
  fi
  command -v gcloud >/dev/null 2>&1 || die "gcloud is not on PATH"
}

# The migration the DATABASE IS AT, measured -- not the chain head.
#
# GET /v1/version reports `schema_revision`, and the deploy never set it, so the
# field arrived null and the web app's status strip rendered a bare "schema="
# on every screen. The obvious fix was to derive the value from the Alembic
# chain, and it was wrong: the chain head on this branch is
# 0009_gemini_embedding_plane, which widens evidence_items.embedding to
# VECTOR(1536), and that revision is deliberately not applied. Its upgrade()
# refuses without PV_EMBEDDING_REWRITE_ACK, the corpus is 18,035 Titan vectors
# at VECTOR(1024), and the live cluster sits at 0009b_kernel_idempotency_grant.
# Publishing the head would have put a confident, specific, wrong revision on
# the endpoint this project offers a judge as its authoritative disclosure
# channel.
#
# So it is measured against the cluster, and when it cannot be measured it is
# left unset. An unset value now renders as an explicit absence marker rather
# than as an empty field, which is true; a guess would not be.
SCHEMA_REVISION=""
if [[ -n "${PV_DB_MIGRATOR:-}" ]]; then
  SCHEMA_REVISION="$(python "${ROOT}/scripts/applied_revision.py" "${PV_DB_MIGRATOR}" 2>/dev/null || true)"
fi
if [[ -z "${SCHEMA_REVISION}" ]]; then
  warn "schema_revision could not be read from the cluster; /v1/version will report it absent"
fi


# A deterministic secret, minted once and reused across revisions. Regenerating
# these on every deploy would invalidate every capability proof and every
# pagination cursor the previous revision issued — which reads as intermittent
# 403s rather than as a rotation, and is exactly the shape of D-08-003.
ensure_secret() {
  local name="$1" value="$2"
  if gcloud secrets describe "${name}" --project "${PROJECT}" >/dev/null 2>&1; then
    # Only add a version when the value actually differs, so re-running `up`
    # does not accumulate a version per invocation.
    local current
    current="$(gcloud secrets versions access latest --secret "${name}" \
                 --project "${PROJECT}" 2>/dev/null || echo '')"
    if [[ "${current}" == "${value}" ]]; then
      ok "secret ${name} is current"
      return
    fi
    printf '%s' "${value}" | gcloud secrets versions add "${name}" \
      --project "${PROJECT}" --data-file=- >/dev/null
    ok "secret ${name} updated"
  else
    printf '%s' "${value}" | gcloud secrets create "${name}" \
      --project "${PROJECT}" --replication-policy=automatic --data-file=- >/dev/null
    ok "secret ${name} created"
  fi
}

#: Where the cluster CA is mounted inside the container, and therefore what the
#: DSNs must name. Any absolute path works; this one is where a reader looks.
CA_MOUNT="/etc/ssl/cockroach/root.crt"

#: Where `ccloud` puts the CA on each platform, tried when PV_CA_CERT_FILE is
#: unset. Note `.env`'s PV_CA_CERT holds an UNEXPANDED "%APPDATA%postgresql..."
#: on Windows, which is why it is not read directly.
_DEFAULT_CA="${APPDATA:-${HOME}/.postgresql}/postgresql/root.crt"
[[ -f "${_DEFAULT_CA}" ]] || _DEFAULT_CA="${HOME}/.postgresql/root.crt"

# Rewrite a DSN so `sslrootcert` names the in-container mount rather than a path
# that exists only on the machine running this script. `sslmode` is left exactly
# as supplied: downgrading it is a security decision and this function does not
# make security decisions.
dsn_for_container() {
  local dsn="$1"
  dsn="$(printf '%s' "${dsn}" | sed -E 's/[?&]sslrootcert=[^&]*//')"
  if [[ "${dsn}" == *"?"* ]]; then
    printf '%s&sslrootcert=%s' "${dsn}" "${CA_MOUNT}"
  else
    printf '%s?sslrootcert=%s' "${dsn}" "${CA_MOUNT}"
  fi
}

ensure_secret_from_file() {
  local name="$1" path="$2"
  if gcloud secrets describe "${name}" --project "${PROJECT}" >/dev/null 2>&1; then
    local current
    current="$(gcloud secrets versions access latest --secret "${name}" \
                 --project "${PROJECT}" 2>/dev/null || echo '')"
    if [[ "${current}" == "$(cat "${path}")" ]]; then
      ok "secret ${name} is current"
      return
    fi
    gcloud secrets versions add "${name}" --project "${PROJECT}" \
      --data-file="${path}" >/dev/null
    ok "secret ${name} updated from ${path}"
  else
    gcloud secrets create "${name}" --project "${PROJECT}" \
      --replication-policy=automatic --data-file="${path}" >/dev/null
    ok "secret ${name} created from ${path}"
  fi
}

runtime_sa() {
  local number
  number="$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)')"
  printf '%s-compute@developer.gserviceaccount.com' "${number}"
}

# ---------------------------------------------------------------------------
# up
# ---------------------------------------------------------------------------

cmd_up() {
  require_config
  cd "${ROOT}"

  step "Project ${PROJECT} · region ${REGION} · tag ${TAG}"
  gcloud config set project "${PROJECT}" >/dev/null 2>&1
  ok "gcloud project set"

  step "Enabling the four APIs this deployment uses"
  gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    --project "${PROJECT}" >/dev/null
  ok "run, cloudbuild, artifactregistry, secretmanager"

  step "Artifact Registry"
  if gcloud artifacts repositories describe "${REPO}" \
       --location "${REGION}" --project "${PROJECT}" >/dev/null 2>&1; then
    ok "repository ${REPO} already exists"
  else
    gcloud artifacts repositories create "${REPO}" \
      --repository-format=docker --location "${REGION}" \
      --description="Provenance deployment images" --project "${PROJECT}" >/dev/null
    ok "repository ${REPO} created"
  fi

  step "Secrets"
  # The four signing keys are minted ONCE and reused. read_or_mint returns the
  # stored value when there is one, so re-running `up` does not rotate them —
  # rotating would invalidate every capability proof and pagination cursor the
  # previous revision issued, which presents as intermittent 403s rather than as
  # a rotation. That is the shape of D-08-003 and it is expensive to diagnose.
  ensure_secret provenance-capability-hmac-key   "$(read_or_mint provenance-capability-hmac-key)"
  ensure_secret provenance-cursor-hmac-key       "$(read_or_mint provenance-cursor-hmac-key)"
  ensure_secret provenance-ingest-alias-hmac-key "$(read_or_mint provenance-ingest-alias-hmac-key)"
  ensure_secret provenance-local-auth-secret     "$(read_or_mint provenance-local-auth-secret)"
  # The three supplied values are written every time, so editing .env.deploy and
  # re-running is how a credential is rotated. The two DSNs are rewritten to
  # point ``sslrootcert`` at the in-container mount path -- see below.
  ensure_secret provenance-db-app-url    "$(dsn_for_container "${COCKROACH_DATABASE_URL}")"
  ensure_secret provenance-db-kernel-url "$(dsn_for_container "${COCKROACH_KERNEL_URL}")"
  ensure_secret provenance-google-api-key "${GOOGLE_API_KEY}"

  # The cluster CA, mounted as a file.
  #
  # This cost two revisions to find and neither error said what was wrong.
  #
  # CockroachDB Cloud presents a CLUSTER-SPECIFIC CA, not a publicly-trusted
  # one. A laptop has that certificate because `ccloud` put it in
  # %APPDATA%/postgresql/root.crt (or ~/.postgresql/root.crt); a container has
  # nothing. With `sslmode=verify-full` and no `sslrootcert`, psycopg looks for
  # ~/.postgresql/root.crt and reports "root certificate file ... does not
  # exist" -- which reads like a misconfigured path rather than a missing
  # deployment artifact.
  #
  # The obvious next move, `sslrootcert=system`, is WRONG here and fails one
  # step later with "certificate verify failed": the system trust store is full
  # of public roots and this cluster is not signed by one of them. That second
  # error is the more misleading of the two, because it looks like the fix did
  # not take effect.
  #
  # So the certificate ships. Not because it is secret -- a CA certificate is
  # public by construction -- but because Secret Manager is how Cloud Run mounts
  # a file into a container, and `--set-secrets PATH=NAME:latest` is the whole
  # mechanism. Downgrading to `sslmode=require` would also "work" and is the
  # trap: `require` accepts ANY certificate, so it turns a verified channel to
  # a database holding the entire corpus into an unverified one, silently, and
  # the only visible difference is that the error goes away.
  if [[ -n "${PV_CA_CERT_FILE:-}" && -f "${PV_CA_CERT_FILE}" ]]; then
    ensure_secret_from_file provenance-db-ca-cert "${PV_CA_CERT_FILE}"
  elif [[ -f "${_DEFAULT_CA}" ]]; then
    ensure_secret_from_file provenance-db-ca-cert "${_DEFAULT_CA}"
  else
    die "cannot find the CockroachDB cluster CA certificate.
  Looked at PV_CA_CERT_FILE and ${_DEFAULT_CA}.
  Download it from the CockroachDB Cloud console (Connect -> CA cert) and set
  PV_CA_CERT_FILE in deploy/.env.deploy to its path.
  Without it the service starts, reports db_ok:false, and the log says
  'certificate verify failed' -- which reads like a bad password."
  fi

  step "Granting the runtime service account read on those secrets"
  local sa; sa="$(runtime_sa)"
  local s
  # All NINE. Two were mounted below and never granted here.
  #
  # `provenance-db-ca-cert` is the one this script spends twenty lines telling
  # you the deploy cannot proceed without, mounted at ${CA_MOUNT} in the
  # control-plane revision -- and it was absent from this loop.
  #
  # It did not fail on this machine, which is why it was not noticed: the
  # runtime service account already held the access from an earlier manual
  # grant. A deploy into a fresh project -- a judge reproducing this -- would
  # have got a revision that could not read its own database certificate.
  #
  # `provenance-api-token` is deliberately NOT here. It does not exist yet at
  # this point: it is signed against the deployed control plane and minted in
  # cmd_up's web step, which grants it there, beside `ensure_secret`.
  #
  # tools/tests/test_deploy_secret_grants.py asserts that every secret this
  # script mounts is granted somewhere in it, so the lists cannot drift apart
  # again wherever the grant happens to live.
  for s in provenance-db-app-url provenance-db-kernel-url provenance-google-api-key \
           provenance-capability-hmac-key provenance-cursor-hmac-key \
           provenance-ingest-alias-hmac-key provenance-local-auth-secret \
           provenance-db-ca-cert; do
    gcloud secrets add-iam-policy-binding "${s}" \
      --member "serviceAccount:${sa}" \
      --role roles/secretmanager.secretAccessor \
      --project "${PROJECT}" >/dev/null 2>&1 || warn "could not bind ${s} (may already be bound)"
  done
  ok "accessor granted to ${sa}"

  step "Building control-plane"
  build_with_docker "deploy/Dockerfile.control-plane" "${CP_IMAGE}"
  ok "${CP_IMAGE}"

  # APP_BASE_URL and WEB_BASE_URL are REQUIRED settings, and this is the
  # chicken-and-egg the first version of this script got wrong.
  #
  # The obvious plan — deploy, read back the URL Cloud Run minted, then update
  # the revision with it — cannot work, because the first revision has to BOOT
  # before there is a URL to read, and `Settings` refuses to construct without
  # them. The container started, `build_app()` raised, uvicorn never bound, and
  # Cloud Run reported the only thing it can see: "failed to start and listen on
  # the port". The cause was two absent strings, four layers up.
  #
  # So the URLs are COMPUTED instead. Cloud Run's hostname is deterministic —
  # https://SERVICE-PROJECTNUMBER.REGION.run.app — and every input is known
  # before the first deploy. They are still read back and corrected below,
  # because a computed value that is never checked against the real one is
  # exactly the "unobserved mapping" this project keeps filing defects about.
  step "Computing the service URLs before deploying"
  local project_number
  project_number="$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)')"
  local cp_url="https://${CP_SERVICE}-${project_number}.${REGION}.run.app"
  local web_url="https://${WEB_SERVICE}-${project_number}.${REGION}.run.app"
  ok "control-plane ${cp_url}"
  ok "web           ${web_url}"

  step "Deploying control-plane"
  gcloud run deploy "${CP_SERVICE}" \
    --project "${PROJECT}" --region "${REGION}" \
    --image "${CP_IMAGE}" \
    --allow-unauthenticated \
    --port 8080 \
    --cpu 1 --memory 1Gi \
    --min-instances 0 \
    --max-instances 1 \
    --timeout 300 \
    --set-env-vars "PV_PLATFORM=local,APP_ENV=demo,LOG_LEVEL=INFO,${SCHEMA_REVISION:+SCHEMA_REVISION=${SCHEMA_REVISION},}GOOGLE_CLOUD_REGION=${REGION},OTEL_SERVICE_NAME=provenance-control-plane,GIT_SHA=${GIT_SHA},APP_BASE_URL=${cp_url},WEB_BASE_URL=${web_url},UPLOAD_URL_TTL_SECONDS=900,DOWNLOAD_URL_TTL_SECONDS=900,PROVENANCE_CAPABILITY_HMAC_KID=k1,PV_AGENT_MODE=LIVE,PV_ACTION_EXECUTION_MODE=ENABLED,PV_LOCAL_OBJECT_ROOT=/var/lib/provenance/objects,GEMINI_REASONING_MODEL_ID=gemini-3.6-flash,GEMINI_EXTRACTION_MODEL_ID=gemini-3.5-flash-lite,GEMINI_REASONING_FALLBACK_MODEL_ID=gemini-3.7-flash,GEMINI_EMBEDDING_MODEL_ID=gemini-embedding-2" \
    --set-secrets "COCKROACH_DATABASE_URL=provenance-db-app-url:latest,COCKROACH_KERNEL_URL=provenance-db-kernel-url:latest,GOOGLE_API_KEY=provenance-google-api-key:latest,PROVENANCE_CAPABILITY_HMAC_KEY=provenance-capability-hmac-key:latest,CURSOR_HMAC_KEY=provenance-cursor-hmac-key:latest,INGEST_ALIAS_HMAC_KEY=provenance-ingest-alias-hmac-key:latest,PV_LOCAL_AUTH_SECRET=provenance-local-auth-secret:latest,${CA_MOUNT}=provenance-db-ca-cert:latest" \
    --quiet >/dev/null \
    || { dump_revision_logs "${CP_SERVICE}"; die "control-plane revision did not start"; }

  local actual_cp
  actual_cp="$(gcloud run services describe "${CP_SERVICE}" --project "${PROJECT}" \
                 --region "${REGION}" --format='value(status.url)')"
  if [[ "${actual_cp}" != "${cp_url}" ]]; then
    warn "computed ${cp_url} but Cloud Run minted ${actual_cp}; correcting"
    cp_url="${actual_cp}"
    gcloud run services update "${CP_SERVICE}" --project "${PROJECT}" --region "${REGION}" \
      --update-env-vars "APP_BASE_URL=${cp_url}" --quiet >/dev/null
  fi
  ok "control-plane serving at ${cp_url}"

  step "Building web"
  build_with_docker "deploy/Dockerfile.web" "${WEB_IMAGE}"
  ok "${WEB_IMAGE}"

  step "Deploying web"
  # PV_API_TOKEN is minted against the deployed control plane's signing secret,
  # server-side only. It carries no NEXT_PUBLIC_ prefix on purpose: Next.js
  # inlines prefixed variables into the browser bundle.
  local api_token
  # --ttl 604800 (seven days), NOT the twelve-hour default.
  #
  # This token is baked into the web revision at deploy time and is the ONLY
  # credential the deployed site authenticates with. The default twelve hours
  # expires while nobody is looking, and the failure is silent in the worst
  # way: the site keeps answering HTTP 200 and renders 401 with no data and no
  # fixture banner, because PV_API_BASE_URL is set and only the token is bad.
  #
  # Measured on 2026-08-30: the deployed token had been expired for fifty
  # hours. The API was healthy, every API-level check passed, and the page a
  # judge would open was dead. Nothing caught it, because the rehearsal
  # authenticated with a token it minted itself rather than the one the
  # revision holds.
  #
  # Seven days outlives any demo window, and `deploy/cloudrun.sh down` takes
  # the services offline after the recording anyway. A token that outlives its
  # service is not a risk; a token that dies before it is the whole problem.
  api_token="$(python "${ROOT}/scripts/mint_local_token.py" --ttl 604800 --quiet 2>/dev/null || echo '')"
  # Into Secret Manager, never onto a command line.
  #
  # This was --set-env-vars until 2026-08-30, which put a LIVE BEARER TOKEN in
  # plaintext into the web service's revision spec -- readable by anyone with
  # roles/viewer via `gcloud run services describe`, and into this machine's
  # shell history and process table besides. The header of this file says
  # exactly why that is wrong, and every other credential here already obeyed
  # it; this one variable was the exception.
  #
  # It matters more than the others, not less. The control plane runs
  # --allow-unauthenticated at the Cloud Run layer, so PV_API_TOKEN is the
  # entire authentication boundary for the API.
  if [[ -n "${api_token}" ]]; then
    ensure_secret provenance-api-token "${api_token}"
    gcloud secrets add-iam-policy-binding provenance-api-token \
      --member "serviceAccount:$(runtime_sa)" \
      --role roles/secretmanager.secretAccessor \
      --project "${PROJECT}" >/dev/null 2>&1 || true
  fi
  if [[ -z "${api_token}" ]]; then
    warn "could not mint PV_API_TOKEN locally; web will start in FIXTURE mode"
    warn "mint one, store it, and mount it BY REFERENCE -- never --update-env-vars:"
    warn "  gcloud secrets versions add provenance-api-token --data-file=-"
    warn "  gcloud run services update ${WEB_SERVICE} --update-secrets PV_API_TOKEN=provenance-api-token:latest"
  fi
  gcloud run deploy "${WEB_SERVICE}" \
    --project "${PROJECT}" --region "${REGION}" \
    --image "${WEB_IMAGE}" \
    --allow-unauthenticated \
    --port 3000 \
    --cpu 1 --memory 512Mi \
    --min-instances 0 --max-instances 2 \
    --set-env-vars "PV_API_BASE_URL=${cp_url},GIT_SHA=${GIT_SHA}" \
    ${api_token:+--set-secrets "PV_API_TOKEN=provenance-api-token:latest"} \
    --quiet >/dev/null \
    || { dump_revision_logs "${WEB_SERVICE}"; die "web revision did not start"; }

  local actual_web
  actual_web="$(gcloud run services describe "${WEB_SERVICE}" --project "${PROJECT}" \
                  --region "${REGION}" --format='value(status.url)')"
  if [[ "${actual_web}" != "${web_url}" ]]; then
    warn "computed ${web_url} but Cloud Run minted ${actual_web}; correcting WEB_BASE_URL"
    web_url="${actual_web}"
    gcloud run services update "${CP_SERVICE}" --project "${PROJECT}" --region "${REGION}" \
      --update-env-vars "WEB_BASE_URL=${web_url}" --quiet >/dev/null
  fi
  ok "web serving at ${web_url}"

  cmd_proof
}

# What Cloud Run will not tell you at the point of failure.
#
# `gcloud run deploy` reports "the user-provided container failed to start and
# listen on the port" for every startup failure, whatever the cause. That
# sentence is true and useless: it describes the symptom Cloud Run can observe
# and says nothing about the exception four layers up. The first run of this
# script died exactly that way, and the real cause was two unset environment
# variables that `Settings` refuses to construct without.
#
# So on failure, fetch what the container actually said. A deploy that fails
# without printing the reason makes the next person re-derive it.
dump_revision_logs() {
  local service="$1"
  printf '\n  --- last 40 log lines for %s ---\n' "${service}" >&2
  gcloud logging read \
    "resource.type=cloud_run_revision AND resource.labels.service_name=${service}" \
    --project "${PROJECT}" --limit 40 --format='value(textPayload)' 2>&1 \
    | sed 's/^/  | /' >&2 || printf '  (could not read logs)\n' >&2
  printf '  --- end ---\n\n' >&2
}

# Build locally and push, rather than `gcloud builds submit`.
#
# `builds submit --tag` assumes a Dockerfile at the context root. Both of these
# live under deploy/ and both need the repository root as context, which that
# form cannot express without a throwaway cloudbuild.yaml per service. Building
# locally keeps one code path for both, and both images are already proven to
# build here.
#
# --platform linux/amd64 is not decoration. Cloud Run runs amd64; an image built
# on an arm64 machine without it pushes happily and then fails at start with
# "exec format error", which reads like a broken entrypoint.
build_with_docker() {
  local dockerfile="$1" image="$2"

  # Cloud Build first, local Docker as the fallback -- deliberately that way
  # round, and it used to be the reverse.
  #
  # Building locally made the deployment path depend on a working Docker
  # daemon on one particular machine. That daemon died twice mid-deploy
  # (`Docker Desktop is unable to start`, then a 500 from the engine pipe)
  # and took the only route to production with it. A deploy that needs one
  # laptop's Docker Desktop is that laptop's deploy, not the project's.
  #
  # Cloud Build needs only gcloud, so a judge can reproduce the image without
  # installing Docker at all. The context it uploads is governed by
  # .gcloudignore, which gcloud reads and .dockerignore is not -- that is why
  # both files exist and why deploy/README.md says to keep them in step.
  if gcloud builds submit \
       --project "${PROJECT}" \
       --config deploy/cloudbuild.yaml \
       --substitutions="_IMAGE=${image},_DOCKERFILE=${dockerfile},_GIT_SHA=${GIT_SHA}" \
       --quiet "${ROOT}" >/dev/null 2>&1; then
    return 0
  fi
  warn "Cloud Build refused; falling back to a local docker build"

  command -v docker >/dev/null 2>&1 || die "Cloud Build failed and docker is not on PATH"
  gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet >/dev/null 2>&1
  docker build --platform linux/amd64 \
    -f "${dockerfile}" --build-arg GIT_SHA="${GIT_SHA}" -t "${image}" "${ROOT}" \
    || die "both Cloud Build and the local docker build failed for ${dockerfile}"
  docker push "${image}" || die "docker push failed for ${image}"
}


read_or_mint() {
  local name="$1" existing
  existing="$(gcloud secrets versions access latest --secret "${name}" \
                --project "${PROJECT}" 2>/dev/null || true)"
  if [[ -n "${existing}" ]]; then
    printf '%s' "${existing}"
  else
    python - <<'PY'
import base64, secrets
print(base64.b64encode(secrets.token_bytes(32)).decode(), end="")
PY
  fi
}

# ---------------------------------------------------------------------------
# proof — what the video has to show
# ---------------------------------------------------------------------------

cmd_proof() {
  require_config
  local cp web
  cp="$(gcloud run services describe "${CP_SERVICE}" --project "${PROJECT}" \
          --region "${REGION}" --format='value(status.url)' 2>/dev/null || echo '')"
  web="$(gcloud run services describe "${WEB_SERVICE}" --project "${PROJECT}" \
          --region "${REGION}" --format='value(status.url)' 2>/dev/null || echo '')"

  [[ -n "${cp}" ]] || die "no ${CP_SERVICE} in ${PROJECT}/${REGION} — run: deploy/cloudrun.sh up"

  cat <<EOF

================================================================================
  DEPLOYED. This is the proof the submission needs.
================================================================================

  Web            ${web}
  Control plane  ${cp}

  The rules ask for visual proof the project was built and deployed on Google
  Cloud. Any ONE of these on screen satisfies it; showing two costs ten seconds
  and removes the argument:

    1. The Cloud Run dashboard, both services listed, region ${REGION}:
       https://console.cloud.google.com/run?project=${PROJECT}

    2. The unauthenticated disclosure endpoint, on camera, at the .run.app host:
       curl -s ${cp}/v1/version

       It carries git_sha, fixture_mode, agent_mode, otlp_export and db_ok.
       Read db_ok — a 200 alone proves only that the process is up.

    3. The live app at ${web} with no fixture banner.

  Verify now:

    curl -s ${cp}/v1/version | python -m json.tool
    curl -s -o /dev/null -w '%{http_code}\\n' ${cp}/v1/healthz     # expect 200
    curl -s -o /dev/null -w '%{http_code}\\n' ${cp}/v1/cases       # expect 401

  When the video is recorded and the submission is in:

    deploy/cloudrun.sh down

  Cloud Run already bills nothing while idle; \`down\` pins max-instances to 0
  so nothing can start one. The images stay in Artifact Registry, so bringing
  it back up is one command and needs no rebuild.

================================================================================
EOF
}

# ---------------------------------------------------------------------------
# down / destroy
# ---------------------------------------------------------------------------

cmd_down() {
  require_config
  local s
  for s in "${CP_SERVICE}" "${WEB_SERVICE}"; do
    if gcloud run services describe "${s}" --project "${PROJECT}" \
         --region "${REGION}" >/dev/null 2>&1; then
      gcloud run services update "${s}" --project "${PROJECT}" --region "${REGION}" \
        --min-instances 0 --max-instances 0 --quiet >/dev/null
      ok "${s} pinned to 0 instances"
    else
      warn "${s} does not exist — nothing to scale down"
    fi
  done
  printf '\n  Both services are off and billing nothing. Bring them back with:\n'
  printf '    gcloud run services update %s --region %s --max-instances 1\n' "${CP_SERVICE}" "${REGION}"
  printf '    gcloud run services update %s --region %s --max-instances 2\n\n' "${WEB_SERVICE}" "${REGION}"
}

cmd_destroy() {
  require_config
  printf '\n  This deletes both Cloud Run services and the %s image repository in\n' "${REPO}"
  printf '  project %s. Secrets are NOT deleted.\n\n  Type the project id to confirm: ' "${PROJECT}"
  local answer; read -r answer
  [[ "${answer}" == "${PROJECT}" ]] || die "not confirmed; nothing was deleted"
  local s
  for s in "${CP_SERVICE}" "${WEB_SERVICE}"; do
    gcloud run services delete "${s}" --project "${PROJECT}" --region "${REGION}" \
      --quiet >/dev/null 2>&1 && ok "deleted ${s}" || warn "${s} was not present"
  done
  gcloud artifacts repositories delete "${REPO}" --location "${REGION}" \
    --project "${PROJECT}" --quiet >/dev/null 2>&1 && ok "deleted repository ${REPO}" \
    || warn "repository ${REPO} was not present"
}

# ---------------------------------------------------------------------------

case "${1:-}" in
  up)      cmd_up ;;
  proof)   cmd_proof ;;
  down)    cmd_down ;;
  destroy) cmd_destroy ;;
  *)
    printf 'usage: %s {up|proof|down|destroy}\n\n' "${0##*/}"
    printf '  up       build, push and deploy both services, then print the proof block\n'
    printf '  proof    re-print the proof block for an already-deployed project\n'
    printf '  down     pin both services to zero instances (billing stops)\n'
    printf '  destroy  delete the services and the image repository\n\n'
    printf 'Configuration goes in deploy/.env.deploy — see deploy/.env.deploy.example\n'
    exit 2 ;;
esac
