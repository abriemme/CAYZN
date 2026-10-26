import asyncio
from datetime import date, timedelta
from decimal import Decimal

import psycopg
from sqlalchemy import select
from test_bookings import PAYLOAD, post

from cayzn import worker
from cayzn.models import (
    ComputeRequest,
    Forecast,
    ForecastPoint,
    OriginDestination,
    RecommendedPrice,
    Service,
)


def pending(session):
    session.expire_all()
    query = session.query(ComputeRequest).order_by(ComputeRequest.queued_at)
    return [p.service_id for p in query]


def request(session, *service_ids):
    session.add_all(ComputeRequest(service_id=i) for i in service_ids)
    session.commit()


def test_bookings_do_not_request_a_job(client, session, plan):
    post(client, PAYLOAD)

    assert pending(session) == []


def test_request_all_queues_services_still_to_depart(
    session, plan, run_worker, conninfo
):
    # The sample plan departs in 2024: only these two are still to depart.
    session.add(Service(name="later", departure_date=date.today() + timedelta(days=1)))
    session.add(Service(name="today", departure_date=date.today()))
    session.commit()
    request(session, 6)  # already waiting: requested once

    async def notified():
        async with await psycopg.AsyncConnection.connect(
            conninfo, autocommit=True
        ) as conn:
            await conn.execute(f"LISTEN {worker.CHANNEL}")
            added = await worker.request_all()  # on run_worker's test engine
            return added, [
                n.channel async for n in conn.notifies(timeout=5, stop_after=1)
            ]

    assert asyncio.run(notified()) == (1, [worker.CHANNEL])
    assert pending(session) == [6, 7]


def test_service_running_elsewhere_is_skipped(session, plan, run_worker, conninfo):
    request(session, 1, 2)

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


def test_failed_job_is_not_retried(session, plan, run_worker, monkeypatch):
    request(session, 1)
    monkeypatch.setattr(worker.revenue_management, "forecast", lambda *_: 1 / 0)

    assert run_worker() == 1
    assert pending(session) == []
    assert session.query(Forecast).count() == 0


def test_service_without_bookings_stores_no_run(session, plan, run_worker):
    request(session, 1)

    assert run_worker() == 1
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
    request(session, 1)
    run_worker()

    [forecast] = session.query(Forecast).all()
    assert forecast.current_day_x == 200
    assert forecast.bookings_as_of == 2
    assert forecast.optimized_at is not None
    ods = {
        od.id: (od.origin.code, od.destination.code)
        for od in session.query(OriginDestination)
    }
    points = session.execute(
        select(ForecastPoint.od_id, ForecastPoint.day_x, ForecastPoint.bookings)
        .where(ForecastPoint.forecast_id == forecast.id)
        .order_by(ForecastPoint.od_id, ForecastPoint.day_x.desc())
    ).all()
    assert len(points) == 3 * 201
    # Every OD, from the current day-x down to day-0: the first point is observed.
    assert {ods[o]: n for o, d, n in points if d == 200} == {
        ("LPD", "MSC"): 1,
        ("PLY", "LPD"): 1,
        ("PLY", "MSC"): 0,
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
