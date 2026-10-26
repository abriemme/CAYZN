import asyncio
from decimal import Decimal

import psycopg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from test_bookings import CANCEL, PAYLOAD, post

from cayzn import worker
from cayzn.models import (
    Booking,
    Forecast,
    OriginDestination,
    PendingForecast,
    RecommendedPrice,
)

OTHER_SERVICE = PAYLOAD | {
    "service_number": "7866",
    "departure_date": "2024-11-15",
    "origin_station_name": "ply",
}


def pending(session):
    session.expire_all()
    query = session.query(PendingForecast).order_by(PendingForecast.queued_at)
    return [p.service_id for p in query]


@pytest.fixture
def conninfo(database_url):
    return database_url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )


@pytest.fixture
def run_worker(database_url, conninfo, monkeypatch):
    """Run the jobs queued so far, on the test database, without delays."""
    monkeypatch.setattr(worker, "FORECAST_SECONDS", (0, 0))
    monkeypatch.setattr(worker, "OPTIMIZATION_SECONDS", (0, 0))
    engine = create_async_engine(database_url, poolclass=NullPool)
    monkeypatch.setattr(worker, "AsyncSessionLocal", async_sessionmaker(engine))

    async def run():
        async with await psycopg.AsyncConnection.connect(
            conninfo, autocommit=True
        ) as conn:
            jobs = 0
            while await worker.run_next(conn):
                jobs += 1
            return jobs

    return lambda: asyncio.run(run())


def test_service_has_at_most_one_waiting_job(client, session, plan):
    post(client, PAYLOAD)
    post(client, PAYLOAD | {"price": 11})
    post(client, PAYLOAD, key="abc")
    post(client, OTHER_SERVICE)

    assert session.query(Booking).count() == 4
    assert pending(session) == [1, 2]


def test_booking_queues_with_a_notification(client, plan, conninfo):
    async def notified():
        async with await psycopg.AsyncConnection.connect(
            conninfo, autocommit=True
        ) as conn:
            await conn.execute(f"LISTEN {worker.CHANNEL}")
            await asyncio.to_thread(post, client, PAYLOAD)
            return [n.channel async for n in conn.notifies(timeout=5, stop_after=1)]

    assert asyncio.run(notified()) == [worker.CHANNEL]


def test_booking_arriving_after_the_job_started_queues_again(
    client, session, plan, run_worker
):
    post(client, PAYLOAD)
    assert run_worker() == 1
    post(client, PAYLOAD | {"price": 11})

    assert pending(session) == [1]


def test_only_applied_cancellations_queue_a_job(client, session, plan, run_worker):
    post(client, PAYLOAD)
    run_worker()
    post(client, PAYLOAD | CANCEL)
    assert pending(session) == [1]
    run_worker()
    post(client, PAYLOAD | CANCEL)  # redelivery: nothing changes
    post(client, PAYLOAD)

    assert pending(session) == []


def test_rejected_booking_queues_nothing(client, session, plan, run_worker):
    post(client, PAYLOAD, key="abc")
    run_worker()

    assert post(client, PAYLOAD | {"price": 11}, key="abc").status_code == 409
    assert post(client, PAYLOAD | {"service_number": "0"}).status_code == 422
    assert pending(session) == []


def test_service_running_elsewhere_is_skipped(
    client, session, plan, run_worker, conninfo
):
    post(client, PAYLOAD)
    post(client, OTHER_SERVICE)

    async def run_while_locked():
        async with await psycopg.AsyncConnection.connect(
            conninfo, autocommit=True
        ) as other:
            # Another worker running service 1.
            await other.execute("SELECT pg_advisory_lock(%s, 1)", (worker.LOCK_SPACE,))
            return await asyncio.to_thread(run_worker)

    assert asyncio.run(run_while_locked()) == 1
    assert pending(session) == [1]
    assert run_worker() == 1
    assert pending(session) == []


def test_failed_job_is_not_retried(client, session, plan, run_worker, monkeypatch):
    post(client, PAYLOAD)
    monkeypatch.setattr(worker.revenue_management, "forecast", lambda *_: 1 / 0)

    assert run_worker() == 1
    assert pending(session) == []
    assert session.query(Forecast).count() == 0


def test_job_stores_forecast_and_recommended_prices(client, session, plan, run_worker):
    post(client, PAYLOAD)  # LPD-MSC, day-200
    post(
        client,
        PAYLOAD
        | {
            "origin_station_name": "ply",
            "destination_station_name": "lpd",
            "sale_datetime": "2024-04-20T08:00:00",  # day-208
        },
    )
    run_worker()

    [forecast] = session.query(Forecast).all()
    assert forecast.current_day_x == 200
    assert forecast.curves["service"]["200"] == 2
    assert len(forecast.curves["service"]) == 201
    ods = {
        od.id: (od.origin.code, od.destination.code)
        for od in session.query(OriginDestination)
    }
    prices = {
        ods[p.od_id]: p.price
        for p in session.query(RecommendedPrice).filter_by(forecast_id=forecast.id)
    }
    # LPD-MSC sells at day-200 only, PLY-LPD stopped selling, PLY-MSC never sold.
    assert prices == {
        ("LPD", "MSC"): Decimal("55.00"),
        ("PLY", "LPD"): Decimal("36.00"),
        ("PLY", "MSC"): Decimal("20.00"),
    }
