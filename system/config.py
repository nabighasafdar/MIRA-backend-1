import os
from pathlib import Path
from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', case_sensitive=True, extra='ignore')
    BROWSER_USE_LOGGING_LEVEL: str = 'info'
    ANONYMIZED_TELEMETRY: bool = False
    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = None
    SKIP_LLM_API_KEY_VERIFICATION: bool = False
    BROWSER_USE_VERSION_CHECK: bool = True
    CHROME_PATH: str | None = None
    IN_DOCKER: bool = Field(default_factory=lambda: os.getenv('IN_DOCKER', 'false').lower()[:1] in 'ty1')
    # Optional: selects default model when Agent is constructed without an explicit llm
    DEFAULT_LLM: str | None = None
    # Agent HTTP API (FastAPI)
    AGENT_API_SECRET: str | None = None
    SUPABASE_URL: str | None = None
    SUPABASE_SERVICE_ROLE_KEY: str | None = None
    CORS_ORIGINS: str = Field(default='http://localhost:3000')
    MIRA_WORKFLOW_OUTPUT_DIR: str | None = None
    MIRA_MOCK_FRONTEND_STREAM: str | None = None

    @computed_field
    @property
    def XDG_CONFIG_HOME(self) -> Path:
        return Path(os.getenv('XDG_CONFIG_HOME', '~/.config')).expanduser().resolve()

    @computed_field
    @property
    def XDG_CACHE_HOME(self) -> Path:
        return Path(os.getenv('XDG_CACHE_HOME', '~/.cache')).expanduser().resolve()

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
CONFIG = Config()