"""Forecast and optimization jobs, queued by POST /bookings, run by `just worker`.

The queue is the `pending_forecasts` table: at most one row per service, taken
with SKIP LOCKED. A per-service advisory lock keeps a service's jobs from
running concurrently; workers skip the services it holds. Failed jobs are not retried: the service's next booking
queues a new one.
"""

import asyncio
import logging
import signal

import psycopg
from sqlalchemy import func, make_url, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from cayzn import revenue_management
from cayzn.db import AsyncSessionLocal
from cayzn.models import (
    Booking,
    Forecast,
    OriginDestination,
    PendingForecast,
    RecommendedPrice,
    Service,
)
from cayzn.settings import settings

log = logging.getLogger(__name__)

CHANNEL = "pending_forecasts"
# First key of the two-int advisory locks: keeps service ids apart from other uses.
LOCK_SPACE = 1
# Woken by NOTIFY; polls too, in case a notification came while disconnected.
POLL_SECONDS = 5.0

# Simulated duration of the placeholder algorithms; tests set them to 0.
FORECAST_SECONDS = 1.0
OPTIMIZATION_SECONDS = 5.0

# Oldest waiting service, skipping those running on another worker: they stay
# queued, and that worker takes them next.
CLAIM = text("""
    DELETE FROM pending_forecasts WHERE service_id = (
        SELECT service_id FROM pending_forecasts p
        WHERE NOT EXISTS (
            SELECT FROM pg_locks
            WHERE locktype = 'advisory'
                AND database = (
                    SELECT oid FROM pg_database WHERE datname = current_database()
                )
                AND classid = :space
                AND objid = p.service_id
        )
        ORDER BY queued_at
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING service_id
""")


async def enqueue_forecast_and_optimize(db: AsyncSession, service_id: int) -> None:
    """Queue the service in `db`'s transaction: queued iff `db` commits.

    A service already waiting stays queued once: its job will read this change.
    """
    await db.execute(
        insert(PendingForecast).values(service_id=service_id).on_conflict_do_nothing()
    )
    # Delivered to the workers on commit only.
    await db.execute(select(func.pg_notify(CHANNEL, "")))


async def run_next(conn: psycopg.AsyncConnection) -> bool:
    """Take the oldest waiting service and run its job; False if none waits.

    `conn` holds the advisory locks: they are freed if the worker dies.
    """
    async with AsyncSessionLocal() as db:
        service_id = await db.scalar(CLAIM, {"space": LOCK_SPACE})
        # Committed now: a booking arriving during the job queues the service again.
        await db.commit()
    if service_id is None:
        return False

    await conn.execute("SELECT pg_advisory_lock(%s, %s)", (LOCK_SPACE, service_id))
    try:
        await forecast_and_optimize(service_id)
    except Exception:
        log.exception("Forecast and optimization of service %s failed", service_id)
    finally:
        await conn.execute(
            "SELECT pg_advisory_unlock(%s, %s)", (LOCK_SPACE, service_id)
        )
    return True


async def work() -> None:
    """Run jobs until SIGINT or SIGTERM, finishing the current one."""
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    # psycopg's own URL scheme, without SQLAlchemy's "+psycopg".
    conninfo = (
        make_url(str(settings.database_url))
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        await conn.execute(f"LISTEN {CHANNEL}")
        log.info("Waiting for jobs")
        while not stop.is_set():
            if not await run_next(conn):
                async for _ in conn.notifies(timeout=POLL_SECONDS, stop_after=1):
                    pass


async def forecast_and_optimize(service_id: int) -> None:
    log.info("Forecast and optimization of service %s", service_id)
    async with AsyncSessionLocal() as db:
        service = await db.get(Service, service_id)
        if service is None:  # Removed from the transport plan since.
            return
        prices = dict(
            (
                await db.execute(
                    select(OriginDestination.id, OriginDestination.price).where(
                        OriginDestination.service_id == service_id
                    )
                )
            ).all()
        )
        bookings = (
            await db.execute(
                select(
                    Booking.od_id, Booking.sale_datetime, Booking.cancel_datetime
                ).where(Booking.service_id == service_id)
            )
        ).all()

        result = revenue_management.forecast(service.departure_date, prices, bookings)
        await asyncio.sleep(FORECAST_SECONDS)
        if result is None:
            return
        current_day_x, curves = result
        forecast = Forecast(
            service_id=service_id, current_day_x=current_day_x, curves=curves
        )
        db.add(forecast)
        await db.flush()
        forecast_id = forecast.id  # Read before commit() expires it.
        await db.commit()

        recommended = revenue_management.optimize(current_day_x, curves, prices)
        await asyncio.sleep(OPTIMIZATION_SECONDS)
        db.add_all(
            RecommendedPrice(forecast_id=forecast_id, od_id=od_id, price=price)
            for od_id, price in recommended.items()
        )
        await db.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(work())
