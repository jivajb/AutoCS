from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "AutoCS"
    debug: bool = False
    log_level: str = "INFO"

    # LLM provider — leave api_key blank to run in mock/simulation mode
    # For OpenRouter set base_url=https://openrouter.ai/api/v1 and use an OR key
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: Optional[str] = None   # override for OpenRouter / other proxies

    # Human-in-the-loop: actions below this confidence score require approval
    hitl_confidence_threshold: float = 0.7

    # SQLite database path
    db_path: str = "autocs.db"

    @property
    def use_llm(self) -> bool:
        return bool(self.openai_api_key)


settings = Settings()
