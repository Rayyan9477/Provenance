"""Deployment unit 3 of 4 — `agent-runtime`.

The LangGraph agent package deployed on Bedrock AgentCore Runtime. It holds no
canonical write credential: its database identity is `pv_agent_reader`, which
reads the five `agent_*_v1` views and is refused the base tables.
"""
