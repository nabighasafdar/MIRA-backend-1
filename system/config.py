import os
from pathlib import Path
from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Config(BaseSettings):
    """MIRA environment configurations using pydantic-settings"""
    
    model_config = SettingsConfigDict(
        env_file='.env', 
        env_file_encoding='utf-8', 
        case_sensitive=True, 
        extra='ignore'
    )

    # Logging & Telemetry
    BROWSER_USE_LOGGING_LEVEL: str = 'info'
    ANONYMIZED_TELEMETRY: bool = False

    # LLM API Keys
    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = None
    SKIP_LLM_API_KEY_VERIFICATION: bool = False
    BROWSER_USE_VERSION_CHECK: bool = True

    # Browser configurations
    CHROME_PATH: str | None = None

    # Hardcoded or env fields
    IN_DOCKER: bool = Field(default_factory=lambda: os.getenv('IN_DOCKER', 'false').lower()[:1] in 'ty1')

    @computed_field
    @property
    def XDG_CONFIG_HOME(self) -> Path:
        return Path(os.getenv('XDG_CONFIG_HOME', '~/.config')).expanduser().resolve()

    @computed_field
    @property
    def XDG_CACHE_HOME(self) -> Path:
        return Path(os.getenv('XDG_CACHE_HOME', '~/.cache')).expanduser().resolve()

    # The remaining properties used by profile.py
    @computed_field
    @property
    def BROWSER_USE_CONFIG_DIR(self) -> Path:
        path = Path(os.getenv('BROWSER_USE_CONFIG_DIR', str(self.XDG_CONFIG_HOME / 'browseruse'))).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @computed_field
    @property
    def BROWSER_USE_PROFILES_DIR(self) -> Path:
        return self.BROWSER_USE_CONFIG_DIR / 'profiles'

    @computed_field
    @property
    def BROWSER_USE_DEFAULT_USER_DATA_DIR(self) -> Path:
        return self.BROWSER_USE_PROFILES_DIR / 'default'

    @computed_field
    @property
    def BROWSER_USE_EXTENSIONS_DIR(self) -> Path:
        path = self.BROWSER_USE_CONFIG_DIR / 'extensions'
        path.mkdir(parents=True, exist_ok=True)
        return path

# Create singleton
CONFIG = Config()
