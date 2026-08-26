from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8"
    )

    app_name: str = "demo-api"
    debug: bool = False
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg_async://postgres:postgres@localhost:5432/demo"
    db_schema: str = "test"


settings = Settings()
