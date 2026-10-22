import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from redis import Redis
from redis_fastapi import AsyncRedisDep, FastAPIRedis
from redis_fastapi import get_settings as get_redis_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cayzn import od_cache
from cayzn.db import SessionLocal, get_db
from cayzn.models import Booking
from cayzn.schemas import BookingIn, BookingOut
from cayzn.settings import settings


def _load_od_cache() -> None:
    with SessionLocal() as session, Redis.from_url(str(settings.redis_url)) as redis:
        od_cache.ensure_loaded(session, redis)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    await asyncio.to_thread(_load_od_cache)
    yield


app = FastAPI(title="CAYZN", lifespan=lifespan)
# Single source of config: otherwise the SDK reads REDIS_URL without .env.local.
get_redis_settings().url = str(settings.redis_url)
# Async Redis pool opened/closed with the app, injected via AsyncRedisDep.
FastAPIRedis(app).lifespan()


DB = Annotated[AsyncSession, Depends(get_db)]


@app.get("/health")
async def health(db: DB) -> dict[str, str]:
    await db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.post("/bookings", status_code=status.HTTP_201_CREATED)
async def create_booking(
    payload: BookingIn, db: DB, redis: AsyncRedisDep
) -> BookingOut:
    key = (
        payload.service_number,
        payload.departure_date,
        payload.origin_station_name,
        payload.destination_station_name,
    )
    found = await od_cache.lookup(redis, key)
    if found is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Unknown OD {payload.origin_station_name}-"
                f"{payload.destination_station_name} for service "
                f"{payload.service_number} on {payload.departure_date}"
            ),
        )
    service_id, od_id = found
    booking = Booking(
        service_name=payload.service_number,
        departure_date=payload.departure_date,
        origin=payload.origin_station_name,
        destination=payload.destination_station_name,
        price=payload.price,
        currency=payload.currency,
        sale_datetime=payload.sale_datetime,
        cancel_datetime=payload.cancel_datetime,
        service_id=service_id,
        od_id=od_id,
    )
    db.add(booking)
    await db.commit()
    return BookingOut.model_validate(booking)
