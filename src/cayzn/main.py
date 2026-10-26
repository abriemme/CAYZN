import asyncio
import base64
import binascii
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from redis import Redis
from redis_fastapi import AsyncRedisDep, FastAPIRedis
from redis_fastapi import get_settings as get_redis_settings
from sqlalchemy import and_, func, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from cayzn import od_cache, revenue_management
from cayzn.bookings import BookingConflict, record_booking
from cayzn.db import SessionLocal, get_db
from cayzn.freshness import runs
from cayzn.models import (
    Booking,
    ForecastPoint,
    OriginDestination,
    PriceChange,
    RecommendedPrice,
    Service,
    Stop,
)
from cayzn.prices import change_price
from cayzn.schemas import (
    BookingIn,
    BookingOut,
    CurvePoint,
    GridOut,
    LoadingCurveOut,
    ODOut,
    PriceChangeOut,
    PriceIn,
    PriceOut,
    ServiceOut,
    ServicesPage,
)
from cayzn.settings import settings
from cayzn.worker import request_compute


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


async def _service(db: AsyncSession, service_id: int) -> Service:
    service = await db.get(Service, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown service")
    return service


async def _od(db: AsyncSession, od_id: int, lock: bool = False) -> OriginDestination:
    query = (
        select(OriginDestination)
        .where(OriginDestination.id == od_id)
        .options(
            selectinload(OriginDestination.origin),
            selectinload(OriginDestination.destination),
        )
    )
    od = await db.scalar(query.with_for_update() if lock else query)
    if od is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown OD")
    return od


def _od_out(od: OriginDestination, recommended: RecommendedPrice | None) -> ODOut:
    return ODOut(
        id=od.id,
        origin=od.origin.code,
        destination=od.destination.code,
        price=od.price,
        recommended_price=recommended.price if recommended else None,
    )


def _encode_cursor(departure_date: date, name: str) -> str:
    raw = f"{departure_date.isoformat()}|{name}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str) -> tuple[date, str]:
    try:
        departure_date, name = base64.urlsafe_b64decode(cursor).decode().split("|", 1)
        return date.fromisoformat(departure_date), name
    except binascii.Error, UnicodeDecodeError, ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid cursor"
        ) from None


@app.get("/services")
async def list_services(
    db: DB,
    departure_date_from: Annotated[
        date | None, Query(description="Defaults to today (server local date)")
    ] = None,
    departure_date_to: Annotated[date | None, Query(description="Inclusive")] = None,
    cursor: Annotated[str | None, Query(description="`next_cursor` of a page")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ServicesPage:
    """Services departing from a date on, with their revenue, booking count and
    minimum price, ordered by departure date then name.
    """
    departure_date_from = departure_date_from or date.today()
    if departure_date_to is not None and departure_date_to < departure_date_from:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="departure_date_to must not precede departure_date_from",
        )

    live = and_(Booking.service_id == Service.id, Booking.cancel_datetime.is_(None))
    revenue = select(func.coalesce(func.sum(Booking.price), 0)).where(live)
    booking_count = select(func.count()).select_from(Booking).where(live)
    min_price = select(func.min(OriginDestination.price)).where(
        OriginDestination.service_id == Service.id
    )
    stmt = (
        select(
            Service.id,
            Service.name,
            Service.departure_date,
            revenue.scalar_subquery().label("revenue"),
            booking_count.scalar_subquery().label("booking_count"),
            min_price.scalar_subquery().label("min_price"),
        )
        .where(Service.departure_date >= departure_date_from)
        .order_by(Service.departure_date, Service.name)
        # One extra row tells whether there is a next page.
        .limit(limit + 1)
    )
    if departure_date_to is not None:
        stmt = stmt.where(Service.departure_date <= departure_date_to)
    if cursor is not None:
        stmt = stmt.where(
            tuple_(Service.departure_date, Service.name) > _decode_cursor(cursor)
        )

    rows = (await db.execute(stmt)).all()
    items = [ServiceOut.model_validate(row) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = _encode_cursor(last.departure_date, last.name)
    return ServicesPage(items=items, next_cursor=next_cursor)


@app.get("/services/{service_id}/ods")
async def service_ods(service_id: int, db: DB) -> GridOut:
    """The service's ODs with their current and last recommended prices."""
    await _service(db, service_id)
    r = await runs(db, service_id)
    optimization_id = r.optimization.id if r.optimization else None
    origin, destination = aliased(Stop), aliased(Stop)
    rows = await db.execute(
        select(
            OriginDestination.id,
            origin.code,
            destination.code,
            OriginDestination.price,
            RecommendedPrice.price,
        )
        .join(origin, OriginDestination.origin)
        .join(destination, OriginDestination.destination)
        .outerjoin(
            RecommendedPrice,
            and_(
                RecommendedPrice.od_id == OriginDestination.id,
                RecommendedPrice.forecast_id == optimization_id,
            ),
        )
        .where(OriginDestination.service_id == service_id)
        .order_by(OriginDestination.id)
    )
    return GridOut(
        service_id=service_id,
        forecast_id=optimization_id,
        ods=[
            ODOut(id=i, origin=o, destination=d, price=p, recommended_price=rp)
            for i, o, d, p, rp in rows
        ],
        freshness=r.freshness,
    )


@app.put(
    "/ods/{od_id}/price",
    responses={409: {"description": "Not the recommended price of that run"}},
)
async def set_od_price(od_id: int, payload: PriceIn, db: DB) -> PriceOut:
    """Change the OD's price, or apply a run's recommendation (`forecast_id`).

    A change requests a new forecast and optimization of the service.
    """
    od = await _od(db, od_id, lock=True)
    if payload.forecast_id is not None:
        recommended = await db.get(RecommendedPrice, (payload.forecast_id, od_id))
        if recommended is None or recommended.price != payload.price:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{payload.price} is not the price recommended by run "
                f"{payload.forecast_id} for OD {od_id}",
            )
    if change := change_price(od, payload.price, "analyst", payload.forecast_id):
        db.add(change)
        await request_compute(db, od.service_id)
    r = await runs(db, od.service_id)
    recommended = (
        await db.get(RecommendedPrice, (r.optimization.id, od_id))
        if r.optimization
        else None
    )
    out = PriceOut(od=_od_out(od, recommended), freshness=r.freshness)
    await db.commit()
    return out


@app.get("/ods/{od_id}/price-history")
async def od_price_history(od_id: int, db: DB) -> list[PriceChangeOut]:
    """The OD's price changes, latest first."""
    await _od(db, od_id)
    changes = await db.scalars(
        select(PriceChange)
        .where(PriceChange.od_id == od_id)
        .order_by(PriceChange.changed_at.desc(), PriceChange.id.desc())
    )
    return [PriceChangeOut.model_validate(c) for c in changes]


@app.get("/services/{service_id}/loading-curve")
async def loading_curve(
    service_id: int, db: DB, od_id: int | None = None
) -> LoadingCurveOut:
    """Cumulative bookings by day-x, of the service or of one of its ODs
    (`od_id`), completed by the last forecast where not yet observed."""
    service = await _service(db, service_id)
    if od_id is not None and (await _od(db, od_id)).service_id != service_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown OD")
    bookings = (
        await db.execute(
            select(Booking.od_id, Booking.sale_datetime, Booking.cancel_datetime).where(
                Booking.service_id == service_id
            )
        )
    ).all()
    observed = revenue_management.observed_curve(
        service.departure_date, bookings, od_id
    )
    r = await runs(db, service_id)
    forecast: list[CurvePoint] = []
    if r.forecast is not None:
        points = (
            select(ForecastPoint.day_x, func.sum(ForecastPoint.bookings))
            .where(ForecastPoint.forecast_id == r.forecast.id)
            .group_by(ForecastPoint.day_x)
            .order_by(ForecastPoint.day_x.desc())
        )
        if od_id is not None:
            points = points.where(ForecastPoint.od_id == od_id)
        if observed:  # observed since the run, the forecast is replaced
            points = points.where(ForecastPoint.day_x < min(observed))
        forecast = [
            CurvePoint(day_x=d, bookings=n) for d, n in await db.execute(points)
        ]
    return LoadingCurveOut(
        service_id=service_id,
        od_id=od_id,
        observed=[CurvePoint(day_x=d, bookings=n) for d, n in observed.items()],
        forecast=forecast,
        freshness=r.freshness,
    )
