"""Application settings loaded from environment variables / .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Turnover Analysis & Alert Tool"
    database_url: str = "sqlite:///./turnover_analysis.db"
    default_approaching_pct: float = 80.0
    default_moderate_band_pct: float = 5.0
    default_significant_band_pct: float = 20.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
