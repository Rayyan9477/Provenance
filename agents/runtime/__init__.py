"""Deployment unit 3 of 4 -- `agent-runtime`.

The agent package. Rewritten 2026-08-24 for the pivot: this was specified for
LangGraph on Bedrock AgentCore and is built on the **`google-genai` SDK**,
which the All Things Agentic Hackathon names as one of four accepted agent
frameworks. Phase 7 was never started, which turned out to be an advantage --
there was no graph code to port, so the layer is written once, natively.

It holds no canonical write credential. Its database identity is
`pv_agent_reader`, which reads the five `agent_*_v1` views and is refused the
base tables outright -- the boundary is a SQL grant, not a prompt instruction.

The prohibition that has to be restated for this stack
-------------------------------------------------------
`00_IMPLEMENTATION_MAP.md` section 12 forbids using "LangGraph Store as product
memory". That ban binds the `google-genai` SDK's and ADK's session and memory
abstractions **equally**, and it is written here rather than assumed to carry
over because a rule phrased against a library nobody uses any more is a rule
nobody applies.

Session state is workflow durability only. If canonical truth is ever read from
an SDK session rather than from the database, the single-canonical-writer
guarantee is gone and nothing reports it -- which is the whole reason the
guarantee is enforced by `tools/write_path_lint.py` and by grants rather than
by convention.
"""
