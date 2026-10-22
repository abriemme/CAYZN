import asyncio
from datetime import date
from pathlib import Path

from redis.asyncio import Redis as AsyncRedis

from cayzn import od_cache
from cayzn.importer import import_transport_plan
from cayzn.models import OriginDestination, Service
from cayzn.settings import settings

FILES = Path(__file__).parents[1] / "files"


def lookup(key):
    async def run():
        async with AsyncRedis.from_url(str(settings.redis_url)) as redis:
            return await od_cache.lookup(redis, key)

    return asyncio.run(run())


def load_plan(session):
    import_transport_plan(session, FILES)
    session.commit()


def test_reload_maps_every_od(session, redis):
    load_plan(session)

    assert od_cache.reload(session, redis) == 5
    assert redis.hlen(od_cache.KEY) == 5

    found = lookup(("9410", date(2024, 11, 14), "lpd", "msc"))
    assert found is not None
    od = session.get(OriginDestination, found[1])
    assert od is not None
    assert (od.origin.code, od.destination.code) == ("LPD", "MSC")
    assert session.get(Service, found[0]) is od.service
    assert lookup(("9410", date(2024, 11, 14), "LPD", "MSC")) == found
    assert lookup(("9410", date(2024, 11, 14), "msc", "lpd")) is None
    assert lookup(("9410", date(2024, 11, 15), "lpd", "msc")) is None


def test_reload_replaces_previous_content(session, redis):
    load_plan(session)
    redis.hset(od_cache.KEY, "stale", "1 1")

    od_cache.reload(session, redis)

    assert not redis.hexists(od_cache.KEY, "stale")
    assert not redis.keys(f"{od_cache.KEY}:loading:*")


def test_reload_of_empty_plan_clears_cache(session, redis):
    redis.hset(od_cache.KEY, "stale", "1 1")

    assert od_cache.reload(session, redis) == 0
    assert not redis.exists(od_cache.KEY)


def test_ensure_loaded_only_loads_missing_cache(session, redis):
    load_plan(session)
    redis.hset(od_cache.KEY, "existing", "1 1")

    od_cache.ensure_loaded(session, redis)
    assert redis.hlen(od_cache.KEY) == 1

    redis.delete(od_cache.KEY)
    od_cache.ensure_loaded(session, redis)
    assert redis.hlen(od_cache.KEY) == 5
