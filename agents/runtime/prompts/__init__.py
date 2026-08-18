"""Prompt assets and the renderer.

Prompt text is byte-exact and owned by `specs/14_PROMPTS.md`. Assets live under
`prompts/<prompt_version>/` and their SHA-256 is asserted against a checked-in
manifest at process start, so a mutated prompt fails the health check rather
than silently changing behaviour.
"""
