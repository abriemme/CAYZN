import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from redis import Redis
from redis_fastapi import AsyncRedisDep, FastAPIRedis
from redis_fastapi import get_settings as get_redis_settings
from sqlalchemy import and_, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from cayzn import od_cache
from cayzn.db import SessionLocal, get_db
from cayzn.models import Booking
from cayzn.schemas import BookingIn, BookingOut
from cayzn.settings import settings
from cayzn.worker import enqueue_forecast_and_optimize


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
    sale = (od_id, payload.sale_datetime, payload.price, payload.currency)

    # The database arbitrates duplicates, including concurrent deliveries:
    # the insert is skipped when the booking already exists.
    insert_stmt = insert(Booking).values(
        idempotency_key=idempotency_key,
        departure_date=payload.departure_date,
        service_id=service_id,
        od_id=od_id,
        price=payload.price,
        currency=payload.currency,
        sale_datetime=payload.sale_datetime,
        cancel_datetime=payload.cancel_datetime,
    )
    if idempotency_key is None:
        insert_stmt = insert_stmt.on_conflict_do_nothing(
            index_elements=[
                "od_id",
                "sale_datetime",
                "price",
                "currency",
                "departure_date",
            ],
            index_where=Booking.idempotency_key.is_(None),
        )
        match = and_(
            Booking.idempotency_key.is_(None),
            tuple_(
                Booking.od_id, Booking.sale_datetime, Booking.price, Booking.currency
            )
            == tuple_(*sale),
        )
    else:
        insert_stmt = insert_stmt.on_conflict_do_nothing(
            index_elements=["idempotency_key", "departure_date"]
        )
        match = Booking.idempotency_key == idempotency_key
    # The partition key: the lookup only opens the month's partition.
    match = and_(match, Booking.departure_date == payload.departure_date)

    booking_id = await db.scalar(insert_stmt.returning(Booking.id))
    if booking_id is not None:
        await enqueue_forecast_and_optimize(db, service_id)
        await db.commit()
        return BookingOut(id=booking_id, service_id=service_id, od_id=od_id)

    # Already received: lock it so concurrent cancellations apply one at a time.
    booking = (await db.scalars(select(Booking).where(match).with_for_update())).one()
    if (
        booking.od_id,
        booking.sale_datetime,
        booking.price,
        booking.currency,
    ) != sale:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Idempotency-Key already used for a different booking",
        )
    if payload.cancel_datetime is not None:
        if booking.cancel_datetime is None:
            booking.cancel_datetime = payload.cancel_datetime
            await enqueue_forecast_and_optimize(db, service_id)
        elif booking.cancel_datetime != payload.cancel_datetime:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="Booking already cancelled at a different time",
            )
    response.status_code = status.HTTP_200_OK
    # Built before commit(): the commit expires the object's attributes.
    out = BookingOut.model_validate(booking)
    await db.commit()
    return out
