"""Dynamic Configuration & Environment Discovery for JARVIS.

Core Invariant: Never hardcode configurations, credentials, endpoints,
filesystem paths, timeouts, or policy values. Everything is discoverable,
parameterized, and loaded via dynamic configuration schemas.
"""

from pathlib import Path
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_default_workspace_dir() -> Path:
    """Dynamically resolve workspace root."""
    current = Path(__file__).resolve()
    # Traverse upwards until we locate ARCHITECTURE.md or workspace marker
    for parent in current.parents:
        if (parent / "ARCHITECTURE.md").exists():
            return parent
    return Path.cwd()


class JarvisSettings(BaseSettings):
    """Authoritative settings for the JARVIS Personal AI Operating System."""

    model_config = SettingsConfigDict(
        env_file=str(get_default_workspace_dir() / "jarvis" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Identity & Voice
    APP_NAME: str = Field(default="JARVIS")
    APP_VERSION: str = Field(default="3.0.0")
    ENVIRONMENT: str = Field(default="production")
    VOICE_DEFAULT_NAME: str = Field(default="Algenib")

    # Dynamic Audio Pipeline Configuration (Microphone & Speaker)
    AUDIO_INPUT_DEVICE_INDEX: Optional[int] = Field(default=None)
    AUDIO_INPUT_SAMPLE_RATE: int = Field(default=16000)
    AUDIO_INPUT_CHANNELS: int = Field(default=1)
    AUDIO_INPUT_CHUNK_MS: int = Field(default=30)
    AUDIO_OUTPUT_SAMPLE_RATE: int = Field(default=24000)
    AUDIO_VAD_ENERGY_THRESHOLD: float = Field(default=15.0)
    AUDIO_DUPLEX_SUPPRESSION: bool = Field(default=True)
    VOICE_DRAIN_HOLD_MS: int = Field(default=250)

    # Dynamic Acoustic Echo Cancellation (AEC) & Reference Loopback Settings
    AUDIO_AEC_ENABLED: bool = Field(default=True)
    AUDIO_AEC_PARTITIONS: int = Field(default=6)
    AUDIO_AEC_STEP_SIZE: float = Field(default=0.25)
    AUDIO_AEC_SUPPRESSION_DB: float = Field(default=30.0)
    AUDIO_AEC_DELAY_MAX_MS: int = Field(default=250)
    AUDIO_AEC_DIAGNOSTICS_DIR: Optional[Path] = Field(default=None)

    # API Credentials (loaded securely via environment / .env, never hardcoded)
    GEMINI_API_KEY: Optional[str] = Field(default=None)
    GROQ_API_KEY: Optional[str] = Field(default=None)

    # Dynamic Paths
    WORKSPACE_DIR: Path = Field(default_factory=get_default_workspace_dir)
    DATA_DIR: Path = Field(default_factory=lambda: get_default_workspace_dir() / "jarvis" / "data")
    DATABASE_PATH: Path = Field(
        default_factory=lambda: get_default_workspace_dir() / "jarvis" / "data" / "jarvis.db"
    )
    LOG_DIR: Path = Field(
        default_factory=lambda: get_default_workspace_dir() / "jarvis" / "data" / "logs"
    )
    ARTIFACTS_DIR: Path = Field(
        default_factory=lambda: get_default_workspace_dir() / "jarvis" / "data" / "artifacts"
    )

    # Runtime Network / Gateway
    GATEWAY_HOST: str = Field(default="127.0.0.1")
    GATEWAY_PORT: int = Field(default=8765)
    GATEWAY_PORT_AUTO_DISCOVERY: bool = Field(default=True)
    GATEWAY_PORT_SEARCH_LIMIT: int = Field(default=50)
    HTTP_PORT: int = Field(default=8000)

    # Timeouts & Budgets
    DEFAULT_TIMEOUT_SECONDS: float = Field(default=60.0)
    MAX_SUBAGENT_DEPTH: int = Field(default=3)
    DEFAULT_MAX_RETRIES: int = Field(default=3)

    # Security & Policy Controls (defaults to True for system safety, bypassed dynamically during interactive live voice sessions)
    REQUIRE_APPROVALS: bool = Field(default=True)

    # Operator & Persona Settings
    USER_CALLSIGN: str = Field(default="Sir")

    # Browser Automation Settings
    BROWSER_HEADLESS: bool = Field(default=False)
    BROWSER_CHANNEL: str = Field(default="chrome")

    # Real-Time Web Search Settings
    WEB_SEARCH_ENDPOINT: str = Field(default="https://html.duckduckgo.com/html/")
    WEB_SEARCH_TIMEOUT_SECONDS: float = Field(default=10.0)
    WEB_SEARCH_MAX_RESULTS: int = Field(default=5)

    # Approved Runtime Models (Strict 6-Model Allowlist)
    ALLOWED_MODEL_FAMILIES: List[str] = Field(
        default_factory=lambda: [
            "Gemini 3.8 Live",
            "GPT-OSS 120B",
            "Qwen 3.8 27B",
            "Gemini 3.1 Flash-Lite",
            "Gemini 3.5 Flash-Lite",
            "Gemma 4 31B",
        ]
    )

    # Dynamic Model Mappings (configurable via environment variables)
    MODEL_MAP_GEMINI_LIVE: str = Field(default="gemini-3.8-live")
    MODEL_MAP_GEMINI_3_1_FLASH_LITE: str = Field(default="gemini-2.5-flash-lite")
    MODEL_MAP_GEMINI_3_5_FLASH_LITE: str = Field(default="gemini-2.5-flash")
    MODEL_MAP_GEMMA_4_31B: str = Field(default="gemini-2.5-flash")
    MODEL_MAP_GPT_OSS_120B: str = Field(default="openai/gpt-oss-120b")
    MODEL_MAP_QWEN_3_8_27B: str = Field(default="qwen/qwen3.8-27b")

    def ensure_directories(self) -> None:
        """Ensure runtime directories exist safely."""
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


# Global singleton instance resolved dynamically at runtime
settings = JarvisSettings()
