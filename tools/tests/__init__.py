"""Tests for the gate and defect tooling in `tools/`.

These live beside the code they test, per the test-placement rule in
`CANONICAL_DECISIONS.md` -> *Repository layout canon*: "A test importing from
exactly one package belongs next to that package."

They are not in `[tool.pytest.ini_options].testpaths` (`packages`, `services`,
`agents`, `tests`), so they are selected explicitly:

    pytest tools/tests -q
"""
