"""Deployment-unit namespace for the agent runtime.

Agents propose; they never write canonical state. `.importlinter` forbids
anything under `agents` from importing
`services.control_plane.app.memory_kernel`.
"""
