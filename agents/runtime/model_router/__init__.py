"""Bedrock model routing.

Both tier ids are read from configuration (`BEDROCK_REASONING_MODEL_ID`,
`BEDROCK_EXTRACTION_MODEL_ID`) and never hard-coded, so a model-access grant is
an environment change rather than a code change. `agent_runs.model_route`
records the id that actually served each run.
"""
