from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration de l'application, lue depuis l'environnement (préfixe SM_)."""

    model_config = SettingsConfigDict(env_prefix="SM_", env_file=".env", extra="ignore")

    app_name: str = "StockManager Web"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False

    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:5173"]

    database_url: str = "postgresql+psycopg://stockmanager:stockmanager@localhost:5432/stockmanager"


@lru_cache
def get_settings() -> Settings:
    return Settings()
