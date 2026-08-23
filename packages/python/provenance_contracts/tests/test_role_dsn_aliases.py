"""One registry for role DSNs, not two that agree until they don't.

The defect
-----------
``Settings`` resolved `pv_kernel_writer` from ``COCKROACH_KERNEL_URL`` and
`pv_migrator` from ``COCKROACH_MIGRATOR_URL``. ``scripts/seed/db.py`` declared
its own ``ROLE_DSN_ENV`` reading ``PV_DB_KERNEL`` and ``PV_DB_MIGRATOR``. The
`.env` on the build machine carries the ``PV_DB_*`` spelling, so **only the app
role resolved in `Settings`** -- the kernel pool silently failed to open while
the app pool opened fine, and the process started anyway.

This is the second time two registries for one fact have cost something here.
The first was worse: with both keys present the seed split **across two
databases** and reported "26 tables checked, 26 match" against a database
holding zero evidence rows, while `manifest_check` validated a third choice.

Why aliases rather than renaming
---------------------------------
Renaming `.env` keys would fix today and break every runbook, gate transcript
and shell script that spells them the old way. ``AliasChoices`` lets one field
answer to both spellings, and ``ROLE_DSN_BINDINGS`` becomes the single place
that says which spellings exist -- which is the property that was missing, not
the particular names.
"""

from __future__ import annotations

import pytest

from provenance_contracts.settings import ROLE_DSN_BINDINGS, Settings

pytestmark = pytest.mark.unit

SENTINEL = "n0t-a-real-password-3f8c1d"


def _dsn(user: str) -> str:
    return f"postgresql://{user}:{SENTINEL}@h.invalid:26257/provenance"


_CORE: dict[str, str] = {
    "PV_PLATFORM": "local",
    "APP_ENV": "local",
    "APP_BASE_URL": "https://api.provenance.invalid",
    "WEB_BASE_URL": "https://app.provenance.invalid",
    "OTEL_SERVICE_NAME": "provenance-control-plane",
    "COCKROACH_DATABASE_URL": _dsn("pv_app_reader_writer"),
    "PROVENANCE_CAPABILITY_HMAC_KEY": "a" * 44,
    "PROVENANCE_CAPABILITY_HMAC_KID": "k1",
    "CURSOR_HMAC_KEY": "b" * 44,
    "INGEST_ALIAS_HMAC_KEY": "c" * 44,
    "UPLOAD_URL_TTL_SECONDS": "900",
    "DOWNLOAD_URL_TTL_SECONDS": "900",
}


def _env(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for name, field in Settings.model_fields.items():
        alias = field.validation_alias
        for spelling in _spellings(alias, name):
            monkeypatch.delenv(spelling, raising=False)
    for binding in ROLE_DSN_BINDINGS.values():
        # `pv_agent_reader` and `pv_ops_reader` carry `env_var=None` on purpose:
        # canon says the agent reader's DSN is not an environment variable at
        # all. Skipping them here is honouring that, not working around it.
        for spelling in (binding.env_var, *binding.aliases):
            if spelling is not None:
                monkeypatch.delenv(spelling, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def _spellings(alias: object, fallback: str) -> list[str]:
    if isinstance(alias, str):
        return [alias]
    choices = getattr(alias, "choices", None)
    if choices:
        return [c for c in choices if isinstance(c, str)]
    return [fallback.upper()]


class TestTheKernelDsnResolvesUnderBothSpellings:
    """The pool that silently failed to open."""

    def test_the_canonical_spelling_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, {**_CORE, "COCKROACH_KERNEL_URL": _dsn("pv_kernel_writer")})
        settings = Settings()  # type: ignore[call-arg]
        assert settings.cockroach_kernel_url is not None

    def test_the_dotenv_spelling_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``PV_DB_KERNEL`` is what the build machine's `.env` actually carries."""
        _env(monkeypatch, {**_CORE, "PV_DB_KERNEL": _dsn("pv_kernel_writer")})
        settings = Settings()  # type: ignore[call-arg]
        assert settings.cockroach_kernel_url is not None, (
            "PV_DB_KERNEL did not resolve; the kernel pool cannot open and the "
            "process starts anyway"
        )

    def test_the_migrator_dotenv_spelling_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, {**_CORE, "PV_DB_MIGRATOR": _dsn("pv_migrator")})
        settings = Settings()  # type: ignore[call-arg]
        assert settings.cockroach_migrator_url is not None

    def test_absent_under_every_spelling_stays_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Optional means optional. It must not acquire a default."""
        _env(monkeypatch, _CORE)
        settings = Settings()  # type: ignore[call-arg]
        assert settings.cockroach_kernel_url is None
        assert settings.cockroach_migrator_url is None


class TestTheRegistryIsTheOnlyPlaceSpellingsAreDeclared:
    def test_every_binding_declares_its_aliases(self) -> None:
        for role, binding in ROLE_DSN_BINDINGS.items():
            assert isinstance(binding.aliases, tuple), role

    def test_the_kernel_binding_knows_the_dotenv_spelling(self) -> None:
        assert "PV_DB_KERNEL" in ROLE_DSN_BINDINGS["pv_kernel_writer"].aliases

    def test_the_migrator_binding_knows_both_dotenv_spellings(self) -> None:
        aliases = ROLE_DSN_BINDINGS["pv_migrator"].aliases
        assert "PV_DB_MIGRATOR" in aliases
        assert "PV_DB_MIGRATOR_CI" in aliases, (
            "the CI spelling must be declared here too, or the seed keeps its own "
            "list and the two drift again"
        )

    def test_no_spelling_is_claimed_by_two_roles(self) -> None:
        """The seed once split across two databases because one key was ambiguous."""
        seen: dict[str, str] = {}
        for role, binding in ROLE_DSN_BINDINGS.items():
            for spelling in (binding.env_var, *binding.aliases):
                if spelling is None:
                    continue  # a role with no env var claims no spelling
                assert spelling not in seen, (
                    f"{spelling!r} is claimed by both {seen[spelling]!r} and {role!r}; "
                    "an ambiguous key is how the seed split across two databases"
                )
                seen[spelling] = role

    def test_the_seed_reads_the_registry_rather_than_its_own_table(self) -> None:
        """Counterfactual for the whole file.

        If ``scripts/seed/db.py`` re-grows a hand-written mapping, the two
        registries can disagree again and nothing else would notice.
        """
        from pathlib import Path

        source = (Path(__file__).resolve().parents[4] / "scripts" / "seed" / "db.py").read_text(
            encoding="utf-8"
        )
        assert "ROLE_DSN_BINDINGS" in source, (
            "scripts/seed/db.py no longer consumes ROLE_DSN_BINDINGS; it has its "
            "own spelling table again"
        )
