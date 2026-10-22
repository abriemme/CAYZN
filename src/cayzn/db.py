from collections.abc import Iterator

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Settings(BaseSettings):
    # Le dernier fichier l'emporte : .env.local surcharge .env.
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"))

    database_url: PostgresDsn = PostgresDsn(
        "postgresql+psycopg://cayzn:cayzn@localhost:5432/cayzn"
    )


settings = Settings()

engine = create_engine(str(settings.database_url))
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    # Noms de contraintes déterministes : Alembic peut les retrouver pour les modifier.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
