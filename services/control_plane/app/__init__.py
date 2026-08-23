"""FastAPI application package for the control plane.

Module boundaries are real even though the deployment is a single container:
`memory_kernel/` is the only package permitted to write canonical tables, and
`tools/write_path_lint.py` (Phase 4) enforces that mechanically.
"""
