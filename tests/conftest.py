import pytest
from fastapi.testclient import TestClient
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis_fastapi import get_async_redis
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from cayzn import od_cache
from cayzn.db import Base, get_db
from cayzn.main import app
from cayzn.settings import settings


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
        yield redis
        redis.delete(od_cache.KEY)


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
    sessions = async_sessionmaker(engine, expire_on_commit=False)

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
