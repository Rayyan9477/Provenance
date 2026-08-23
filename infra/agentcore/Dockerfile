# syntax=docker/dockerfile:1
#
# Deployment unit 3: agent-runtime.
#
# AgentCore Runtime requirements this image must satisfy (40_INFRA_IAC.md
# section 9.1), each of which is a hard failure rather than a preference:
#
#   - linux/arm64 only. Build with:
#       docker buildx build --platform linux/arm64 --provenance=false \
#         -f infra/agentcore/Dockerfile -t <registry>/provenance/agent-runtime:sha-<sha> --push .
#     ``--provenance=false`` is not a naming pun: buildx's attestation manifests
#     confuse some registry consumers and the runtime wants a plain
#     single-platform image.
#   - Listens on port 8080.
#   - Serves POST /invocations (the agent entry point) and GET /ping (health).
#   - Stateless per invocation. LangGraph checkpoints are transient only;
#     deleting every checkpoint must leave product memory correct.
#
# Build context is the repository root, not this directory, because the image
# needs packages/python/ as well as agents/.

FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim AS base

ARG BUILD_SHA=unset
ENV BUILD_SHA=${BUILD_SHA} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first so a source-only change does not reinstall the world.
COPY pyproject.toml /app/pyproject.toml
COPY packages/python /app/packages/python
RUN python -m pip install --upgrade pip \
 && python -m pip install "/app/packages/python/provenance_contracts" \
                          "/app/packages/python/provenance_telemetry" \
 && python -m pip install "langgraph>=0.2,<1" "boto3>=1.35" \
                          "fastapi>=0.115,<1" "uvicorn[standard]>=0.30,<1"

COPY agents /app/agents
COPY infra/agentcore/mcp.json /app/infra/agentcore/mcp.json

# Never root. The runtime does not require it and an evidence-adjacent container
# that runs as root is a gift to anything that gets code execution.
RUN useradd --create-home --uid 10001 provenance \
 && chown -R provenance:provenance /app
USER provenance

EXPOSE 8080

# GET /ping must answer while a graph is running, so the health check exercises
# it rather than the invocation path.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/ping',timeout=2)"

CMD ["uvicorn", "agents.runtime.server:app", "--host", "0.0.0.0", "--port", "8080"]
