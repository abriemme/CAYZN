import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from redis import Redis
from redis_fastapi import AsyncRedisDep, FastAPIRedis
from redis_fastapi import get_settings as get_redis_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cayzn import od_cache
from cayzn.bookings import BookingConflict, record_booking
from cayzn.db import SessionLocal, get_db
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


@app.post(
    "/bookings",
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "Booking already received (cancellation applied)"},
        409: {"description": "Idempotency key or cancellation conflicts"},
    },
)
async def create_booking(
    payload: BookingIn,
    db: DB,
    redis: AsyncRedisDep,
    response: Response,
    idempotency_key: Annotated[str | None, Header(min_length=1, max_length=255)] = None,
) -> BookingOut:
    found = await od_cache.lookup(
        redis,
        (
            payload.service_number,
            payload.departure_date,
            payload.origin_station_name,
            payload.destination_station_name,
        ),
    )
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
    try:
        booking_id, created = await record_booking(
            db, payload, service_id, od_id, idempotency_key
        )
    except BookingConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e)) from e
    if not created:
        response.status_code = status.HTTP_200_OK
    return BookingOut(id=booking_id, service_id=service_id, od_id=od_id)
