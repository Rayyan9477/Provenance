"""Deployment unit 2 of 4 — `control-plane`.

One FastAPI container on AWS App Runner containing the API, retrieval, the
Memory Kernel, State Proof, action-policy logic, and the internal tool
endpoints (`00_IMPLEMENTATION_MAP.md` section 4.2). Served as
`services.control_plane.app.main:app`.

This is the only process permitted to hold `pv_kernel_writer` credentials.
"""
