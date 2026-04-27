from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    orbit_api_key: str
    orbit_base_url: str = "https://gravitee.info"

    gemini_api_key: str
    gemini_api_key_2: str = ""
    gemini_base_url: str = "https://dev-org-elesh-nxt.eu.gateway.gravitee.io/ask-gravitee-gemini-api/v1"
    gemini_model: str = "gemini-2.0-flash"

    hubspot_api_key: str
    hubspot_base_url: str = "https://api.hubapi.com"

    google_credentials_file: str = "/Users/shashank.maurya/orbit-notes-sa.json"

    database_url: str = "sqlite+aiosqlite:///./data/orbit_notes.db"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
