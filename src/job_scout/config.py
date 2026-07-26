"""Application configuration loaded from the environment and ``.env``.

A single ``Settings`` object holds every setting. Secrets use ``SecretStr`` so
they never appear in logs or trace metadata by accident. Each field's ``.env``
name is documented in ``.env.example``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read once from the environment or ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    scout_model: str = Field(default="anthropic:claude-sonnet-4-20250514", alias="SCOUT_MODEL")
    scout_tailor_model: str = Field(default="anthropic:claude-sonnet-4-20250514", alias="SCOUT_TAILOR_MODEL")
    ollama_host: str = Field(default="", alias="OLLAMA_HOST")

    anthropic_api_key: SecretStr = Field(default=SecretStr(""), alias="ANTHROPIC_API_KEY")
    openai_api_key: SecretStr = Field(default=SecretStr(""), alias="OPENAI_API_KEY")

    opik_api_key: SecretStr = Field(default=SecretStr(""), alias="OPIK_API_KEY")
    opik_workspace: str = Field(default="", alias="OPIK_WORKSPACE")
    opik_project_name: str = Field(default="job-scout", alias="OPIK_PROJECT_NAME")
    opik_enabled: bool = Field(default=True, alias="OPIK_ENABLED")

    jsearch_api_key: SecretStr = Field(default=SecretStr(""), alias="JSEARCH_API_KEY")
    adzuna_app_id: SecretStr = Field(default=SecretStr(""), alias="ADZUNA_APP_ID")
    adzuna_app_key: SecretStr = Field(default=SecretStr(""), alias="ADZUNA_APP_KEY")

    max_llm_calls_per_run: int = Field(default=25, alias="MAX_LLM_CALLS_PER_RUN")

    cache_path: str = Field(default="", alias="CACHE_PATH")
    scout_db_path: str = Field(default="data/scout.db", alias="SCOUT_DB_PATH")

    @field_validator("opik_workspace", "opik_project_name", "scout_model", "scout_tailor_model", mode="before")
    @classmethod
    def _drop_inline_comment(cls, value: object) -> object:
        """Treat a value that is only a ``# comment`` as empty.

        Guards the common ``.env`` mistake of leaving a key blank but keeping its
        trailing comment, which some parsers read as the value.
        """
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("#"):
                return ""
        return value

    @property
    def has_jsearch(self) -> bool:
        """Whether a JSearch API key is configured."""
        return bool(self.jsearch_api_key.get_secret_value())

    @property
    def has_adzuna(self) -> bool:
        """Whether both Adzuna credentials are configured."""
        return bool(self.adzuna_app_id.get_secret_value() and self.adzuna_app_key.get_secret_value())

    @property
    def has_opik(self) -> bool:
        """Whether Opik tracing is enabled and has an API key."""
        return self.opik_enabled and bool(self.opik_api_key.get_secret_value())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
