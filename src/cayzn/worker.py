"""Forecast and optimization jobs, run by `just worker`.

Usage: python -m cayzn.worker              run the requested jobs
       python -m cayzn.worker request-all  request every service still to depart

The queue is the `compute_requests` table: at most one row per service, taken
with SKIP LOCKED. Services are requested daily (`request-all`, after the
nightly import) and when an analyst changes a price. A per-service advisory
lock keeps a service's jobs from running concurrently; workers skip the
services it holds. Failed jobs are not retried: the next request runs a new one.
"""

import asyncio
import logging
import signal
import sys

import psycopg
from sqlalchemy import func, make_url, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from cayzn import revenue_management
from cayzn.db import AsyncSessionLocal
from cayzn.models import (
    Booking,
    ComputeRequest,
    Forecast,
    ForecastPoint,
    OriginDestination,
    RecommendedPrice,
    Service,
)
from cayzn.settings import settings

log = logging.getLogger(__name__)

CHANNEL = "compute_requests"
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
    DELETE FROM compute_requests WHERE service_id = (
        SELECT service_id FROM compute_requests p
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


async def request_compute(db: AsyncSession, service_id: int) -> None:
    """Queue the service in `db`'s transaction: queued iff `db` commits.

    A service already waiting stays queued once: its job will read this change.
    """
    await db.execute(
        insert(ComputeRequest).values(service_id=service_id).on_conflict_do_nothing()
    )
    # Delivered to the workers on commit only.
    await db.execute(select(func.pg_notify(CHANNEL, "")))


async def request_all() -> int:
    """Queue every service still to depart; return how many were added."""
    async with AsyncSessionLocal() as db:
        added = await db.execute(
            insert(ComputeRequest)
            .from_select(
                ["service_id"],
                select(Service.id).where(Service.departure_date >= func.current_date()),
            )
            .on_conflict_do_nothing()
            .returning(ComputeRequest.service_id)
        )
        count = len(added.all())
        await db.execute(select(func.pg_notify(CHANNEL, "")))
        await db.commit()
    return count


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
            service_id=service_id,
            current_day_x=current_day_x,
            # Same count as `freshness.bookings_count`.
            bookings_as_of=sum(1 + (cancel is not None) for _, _, cancel in bookings),
        )
        db.add(forecast)
        await db.flush()
        forecast_id = forecast.id  # Read before commit() expires it.
        db.add_all(
            ForecastPoint(forecast_id=forecast_id, od_id=od_id, day_x=d, bookings=n)
            for od_id in prices
            for d, n in curves[str(od_id)].items()
        )
        await db.commit()

        recommended = revenue_management.optimize(current_day_x, curves, prices)
        await asyncio.sleep(OPTIMIZATION_SECONDS)
        db.add_all(
            RecommendedPrice(forecast_id=forecast_id, od_id=od_id, price=price)
            for od_id, price in recommended.items()
        )
        await db.execute(
            update(Forecast)
            .where(Forecast.id == forecast_id)
            .values(optimized_at=func.now())
        )
        await db.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if sys.argv[1:] == ["request-all"]:
        print(f"{asyncio.run(request_all())} services requested")
    elif sys.argv[1:]:
        sys.exit("usage: python -m cayzn.worker [request-all]")
    else:
        asyncio.run(work())
