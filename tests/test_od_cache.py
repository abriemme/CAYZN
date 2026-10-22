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


KNOWN = ("9410", date(2024, 11, 14), "LPD", "MSC")


def test_found_od_is_served_locally_without_redis(session, redis):
    load_plan(session)
    od_cache.reload(session, redis)
    found = lookup(KNOWN)

    redis.delete(od_cache.KEY)

    assert lookup(KNOWN) == found
    assert lookup(("9410", date(2024, 11, 14), "lpd", "msc")) == found


def test_missing_od_is_not_cached_locally(session, redis):
    assert lookup(KNOWN) is None

    load_plan(session)
    od_cache.reload(session, redis)

    assert lookup(KNOWN) is not None


def test_least_recently_used_entry_is_evicted(session, redis, monkeypatch):
    monkeypatch.setattr(od_cache, "LOCAL_MAX_ENTRIES", 2)
    load_plan(session)
    od_cache.reload(session, redis)
    ply_lpd = ("9410", date(2024, 11, 14), "PLY", "LPD")
    ply_msc = ("9410", date(2024, 11, 14), "PLY", "MSC")

    lookup(KNOWN)
    lookup(ply_lpd)
    lookup(KNOWN)  # KNOWN becomes the most recently used
    lookup(ply_msc)  # evicts ply_lpd
    redis.delete(od_cache.KEY)

    assert lookup(KNOWN) is not None
    assert lookup(ply_msc) is not None
    assert lookup(ply_lpd) is None
