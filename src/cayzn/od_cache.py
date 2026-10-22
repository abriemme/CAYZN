"""Redis cache: (service_number, departure_date, origin, destination) -> (service_id, od_id).

A single Redis hash holds the whole transport plan. The API loads it on startup
if it does not exist, and the nightly import reloads it. A reload writes a
temporary hash and then renames it: readers see the old plan or the new one,
never a half-loaded plan.
"""

import json
from datetime import date
from itertools import batched
from uuid import uuid4

from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.asyncio import RedisCluster as AsyncRedisCluster
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from cayzn.models import OriginDestination, Service, Stop

KEY: str = "cayzn:ods"
_BATCH = 10_000

ODKey = tuple[str, date, str, str]


def _field(key: ODKey) -> str:
    service_number, departure_date, origin, destination = key
    # JSON rather than a separator: no service name can make a field ambiguous.
    # Station codes are uppercased so a lookup never misses on case alone.
    return json.dumps(
        [
            service_number,
            departure_date.isoformat(),
            origin.upper(),
            destination.upper(),
        ]
    )


def reload(session: Session, redis: Redis) -> int:
    """Replace the cache with the plan in the database. Return the number of ODs loaded."""
    origin, destination = aliased(Stop), aliased(Stop)
    rows = session.execute(
        select(
            Service.name,
            Service.departure_date,
            origin.code,
            destination.code,
            Service.id,
            OriginDestination.id,
        )
        .join(OriginDestination.service)
        .join(origin, OriginDestination.origin)
        .join(destination, OriginDestination.destination)
    ).all()
    if not rows:
        redis.delete(KEY)
        return 0

    tmp = f"{KEY}:loading:{uuid4().hex}"
    try:
        for chunk in batched(rows, _BATCH):
            redis.hset(
                tmp,
                mapping={
                    _field((name, day, o, d)): f"{service_id} {od_id}"
                    for name, day, o, d, service_id, od_id in chunk
                },
            )
        redis.rename(tmp, KEY)
    finally:
        redis.delete(tmp)  # no-op once renamed; cleans up an interrupted load
    return len(rows)


def ensure_loaded(session: Session, redis: Redis) -> None:
    if not redis.exists(KEY):
        reload(session, redis)


async def lookup(
    redis: AsyncRedis | AsyncRedisCluster, key: ODKey
) -> tuple[int, int] | None:
    value = await redis.hget(KEY, _field(key))
    if value is None:
        return None
    service_id, od_id = map(int, value.split())
    return service_id, od_id
