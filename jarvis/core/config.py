"""JARVIS Typed System Configuration.

Manages environment-driven, strongly typed settings conforming to the canonical architecture.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Base directory for the repository
DEFAULT_BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """System-wide configuration model for JARVIS v1.0.0."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Runtime Environment ---
    ENVIRONMENT: Literal["development", "test", "staging", "production"] = "development"
    DEBUG: bool = False
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_JSON: bool = False

    # --- Directory Paths ---
    BASE_DIR: Path = DEFAULT_BASE_DIR
    DATA_DIR: Path = Field(default_factory=lambda: DEFAULT_BASE_DIR / "data")
    ARTIFACTS_DIR: Path = Field(default_factory=lambda: DEFAULT_BASE_DIR / "data" / "artifacts")
    SQLITE_DB_PATH: Path = Field(default_factory=lambda: DEFAULT_BASE_DIR / "data" / "jarvis.db")

    # --- Security & Policy Plane ---
    AUTONOMY_LEVEL: int = Field(
        default=1,
        description="Global autonomy level (0: Read-only, 1: Human-gated, 2: Mixed, 3: High, 4: Supervised, 5: Fully autonomous)",
    )
    VAULT_ENCRYPTION_KEY: SecretStr | None = None
    REQUIRE_COMMIT_TIME_AUTH: bool = True
    APPROVAL_TTL_SECONDS: int = 300  # 5 minutes default TTL

    # --- Execution & Budget Limits ---
    GRAPH_STEP_BUDGET: int = Field(default=50, ge=1, le=500)
    TASK_TIMEOUT_SECONDS: int = Field(default=600, ge=10, le=86400)
    MAX_SUBAGENTS: int = Field(default=4, ge=1, le=10)

    # --- Model Provider API Keys ---
    GEMINI_API_KEY: SecretStr | None = None
    GROQ_API_KEY: SecretStr | None = None
    OPENROUTER_API_KEY: SecretStr | None = None

    # --- Model Identifiers (Frozen Canonical Model Roster) ---
    PRIMARY_MODEL_ID: str = "gemini-2.5-flash"
    COMPLEX_REASONING_MODEL_ID: str = "gemini-2.5-pro"
    FAST_MODEL_ID: str = "qwen/qwen3.8-27b"
    LOCAL_AUTONOMOUS_MODEL_ID: str = "gemma-4-31b-it"
    BACKUP_MODEL_ID: str = "openai/gpt-oss-120b"
    EMBEDDING_MODEL_ID: str = "gemini-embedding-2"
    REALTIME_VOICE_MODEL_ID: str = "gemini-3.8-live"
    REALTIME_VOICE_THINKING_MODEL_ID: str = "gemini-3.8-live-extended-thinking"
    COMPRESSION_MODEL_ID: str = "gemini-3.8-flash-lite-context-compress"
    VOICE_DEFAULT_NAME: str = "Puck"

    @field_validator("AUTONOMY_LEVEL")
    @classmethod
    def validate_autonomy(cls, v: int) -> int:
        if not (0 <= v <= 5):
            raise ValueError(f"AUTONOMY_LEVEL must be between 0 and 5 inclusive, got {v}")
        return v

    def ensure_directories(self) -> None:
        """Ensure all required local directories exist."""
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        self.SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton instance of Settings."""
    settings = Settings()
    settings.ensure_directories()
    return settings
