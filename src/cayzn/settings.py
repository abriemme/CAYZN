"""Application settings: environment, then `.env`, then `.env.local`."""

from pydantic import PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # The last file wins: .env.local overrides .env.
        env_file=(".env", ".env.local"),
        # .env files also hold other tools' settings (the SDK's REDIS_*).
        extra="ignore",
    )

    database_url: PostgresDsn = PostgresDsn(
        "postgresql+psycopg://cayzn:cayzn@localhost:5432/cayzn"
    )
    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")


settings = Settings()
