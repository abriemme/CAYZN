"""Last runs of a service, and how fresh they are, for every API response."""

from dataclasses import dataclass

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cayzn.models import Booking, ComputeRequest, Forecast
from cayzn.schemas import Freshness


@dataclass
class Runs:
    forecast: Forecast | None  # last forecast
    optimization: Forecast | None  # last forecast with recommended prices
    freshness: Freshness


async def runs(db: AsyncSession, service_id: int) -> Runs:
    last = select(Forecast).where(Forecast.service_id == service_id)
    forecast = await db.scalar(last.order_by(Forecast.id.desc()).limit(1))
    if forecast is not None and forecast.optimized_at is not None:
        optimization = forecast
    else:
        optimization = await db.scalar(
            last.where(Forecast.optimized_at.is_not(None))
            .order_by(Forecast.id.desc())
            .limit(1)
        )
    # Same count as the worker's `bookings_as_of`: sales plus cancellations.
    bookings = await db.scalar(
        select(func.count() + func.count(Booking.cancel_datetime)).where(
            Booking.service_id == service_id
        )
    )
    pending = await db.scalar(
        select(exists().where(ComputeRequest.service_id == service_id))
    )
    return Runs(
        forecast,
        optimization,
        Freshness(
            forecast_computed_at=forecast.computed_at if forecast else None,
            optimization_computed_at=(
                optimization.optimized_at if optimization else None
            ),
            stale=(forecast.bookings_as_of if forecast else 0) != bookings,
            pending=bool(pending),
        ),
    )
