import asyncio
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis_fastapi import get_async_redis
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from cayzn import od_cache, worker
from cayzn.db import Base, get_db
from cayzn.importer import import_transport_plan
from cayzn.main import app
from cayzn.settings import settings

FILES = Path(__file__).parents[1] / "files"


@pytest.fixture(scope="session")
def database_url(worker_id):
    """Throwaway Postgres database per xdist worker, on the DATABASE_URL server."""
    server = make_url(str(settings.database_url))
    name = f"{server.database}_test_{worker_id}"
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    url = server.set(database=name)
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()

    yield url

    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def redis_key(worker_id):
    """Cache key specific to each xdist worker, on the REDIS_URL Redis."""
    od_cache.KEY = f"cayzn:test:{worker_id}:ods"


@pytest.fixture
def redis():
    with Redis.from_url(str(settings.redis_url)) as redis:
        redis.delete(od_cache.KEY)
        # Ids restart at 1 after TRUNCATE: a previous test's entries would be wrong.
        od_cache.clear_local()
        yield redis
        redis.delete(od_cache.KEY)
        od_cache.clear_local()


@pytest.fixture
def session(database_url):
    engine = create_engine(database_url)
    with Session(engine) as session:
        yield session
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    engine.dispose()


@pytest.fixture
def client(session, redis, database_url):
    # No `with`: no lifespan, so nothing is loaded from the real database.
    # NullPool and one Redis client per request: nothing is kept from one
    # TestClient event loop to the next.
    engine = create_async_engine(database_url, poolclass=NullPool)
    sessions = async_sessionmaker(engine)

    async def get_test_db():
        async with sessions() as db:
            yield db

    async def get_test_redis():
        async with AsyncRedis.from_url(str(settings.redis_url)) as cache:
            yield cache

    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_async_redis] = get_test_redis
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def plan(session, redis):
    import_transport_plan(session, FILES)
    session.commit()
    od_cache.reload(session, redis)


@pytest.fixture
def conninfo(database_url):
    return database_url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )


@pytest.fixture
def run_worker(database_url, conninfo, monkeypatch):
    """Run the jobs requested so far, on the test database, without delays."""
    monkeypatch.setattr(worker, "FORECAST_SECONDS", (0, 0))
    monkeypatch.setattr(worker, "OPTIMIZATION_SECONDS", (0, 0))
    engine = create_async_engine(database_url, poolclass=NullPool)
    monkeypatch.setattr(worker, "AsyncSessionLocal", async_sessionmaker(engine))

    async def run():
        async with await psycopg.AsyncConnection.connect(
            conninfo, autocommit=True
        ) as conn:
            jobs = 0
            while await worker.run_next(conn):
                jobs += 1
            return jobs

    return lambda: asyncio.run(run())
