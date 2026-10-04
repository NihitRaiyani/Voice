"""Database commands need database credentials only and conceal invalid secrets."""

import pytest
from pydantic import ValidationError
from roma.core.migration_settings import MigrationSettings


def test_database_only_environment_needs_no_voice_provider_settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://demo:secret@localhost/roma")
    for name in ("OPENAI_API_KEY", "SARVAM_API_KEY", "TWILIO_AUTH_TOKEN", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    settings = MigrationSettings(_env_file=None)
    assert settings.require_url() == "postgresql+asyncpg://demo:secret@localhost/roma"
    assert "secret" not in repr(settings)


def test_database_settings_read_dotenv_and_ignore_unrelated_fields(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config = tmp_path / ".env"
    config.write_text(
        "DATABASE_URL=postgresql+asyncpg://localhost/roma\nUNRELATED_OPTION=value\n"
    )
    assert (
        MigrationSettings(_env_file=config).require_url()
        == "postgresql+asyncpg://localhost/roma"
    )


def test_missing_database_url_has_bounded_error(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        MigrationSettings(_env_file=None).require_url()


@pytest.mark.parametrize(
    "url", ["sqlite:///demo", "mysql://demo:private-secret@localhost/roma"]
)
def test_wrong_database_scheme_does_not_expose_input(url):
    with pytest.raises(ValidationError) as error:
        MigrationSettings(_env_file=None, database_url=url)
    assert "PostgreSQL" in str(error.value)
    assert "private-secret" not in str(error.value)
