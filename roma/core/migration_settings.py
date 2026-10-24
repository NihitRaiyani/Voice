"""Database-only configuration for migrations and separate development seeds."""

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def asyncpg_url(value: str) -> str:
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    if not value.startswith("postgresql+asyncpg://"):
        raise ValueError("DATABASE_URL must use PostgreSQL with the asyncpg driver")
    return value


class MigrationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        env_ignore_empty=True,
        hide_input_in_errors=True,
    )
    database_url: SecretStr = SecretStr("")
    app_env: str = "dev"

    @field_validator("database_url")
    @classmethod
    def normalize_url(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        return SecretStr(asyncpg_url(raw)) if raw else value

    def require_url(self) -> str:
        raw = self.database_url.get_secret_value()
        if not raw:
            raise RuntimeError("DATABASE_URL is required for database operations")
        return raw


def current_schema_head() -> str:
    """Read the repository's Alembic head without connecting to a database."""
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'migrations'))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError('No migration head is configured')
    return head
