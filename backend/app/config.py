"""Application settings loaded from environment variables / .env."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Turnover Analysis & Alert Tool"
    database_url: str = "sqlite:///./turnover_analysis.db"
    default_approaching_pct: float = 80.0
    default_moderate_band_pct: float = 5.0
    default_significant_band_pct: float = 20.0
    # Browser origins allowed to call the API (the React frontend), comma-separated.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Also allowed: any port on this machine, so the dev server works whichever port it picks.
    cors_origin_regex: str = r"http://(localhost|127\.0\.0\.1):\d+"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
