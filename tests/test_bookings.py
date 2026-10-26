import asyncio
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from cayzn import od_cache
from cayzn.importer import import_transport_plan
from cayzn.main import app
from cayzn.models import Booking

CANCEL = {"cancel_datetime": "2024-04-29T10:00:00"}

FILES = Path(__file__).parents[1] / "files"

PAYLOAD = {
    "service_number": "9410",
    "departure_date": "2024-11-14",
    "origin_station_name": "lpd",
    "destination_station_name": "msc",
    "price": 10,
    "currency": "EUR",
    "sale_datetime": "2024-04-28T04:55:27",
    "cancel_datetime": None,
}
DEPARTURE = date(2024, 11, 14)


def test_booking_is_stored_and_linked(client, session, plan):
    response = client.post(
        "/bookings", json=PAYLOAD | {"cancel_datetime": "2024-04-29T10:00:00"}
    )

    assert response.status_code == 201
    booking = session.get(Booking, (response.json()["id"], DEPARTURE))
    assert booking is not None
    assert booking.price == Decimal(10)
    assert booking.sale_datetime == datetime(2024, 4, 28, 4, 55, 27)
    assert booking.cancel_datetime == datetime(2024, 4, 29, 10)
    assert (booking.od.origin.code, booking.od.destination.code) == ("LPD", "MSC")
    assert booking.service is booking.od.service
    assert booking.service.name == "9410"
    assert response.json() == {
        "id": booking.id,
        "service_id": booking.service_id,
        "od_id": booking.od_id,
    }
    # Stored in the partition of the departure month, with its plan.
    assert (
        session.scalar(
            text("SELECT tableoid::regclass::text FROM bookings WHERE id = :id"),
            {"id": booking.id},
        )
        == "bookings_2024_11"
    )


@pytest.mark.parametrize(
    ("origin", "destination"), [("lpd", "msc"), ("LPD", "MSC"), (" Lpd ", "mSc")]
)
def test_station_codes_are_case_insensitive(client, session, plan, origin, destination):
    response = client.post(
        "/bookings",
        json=PAYLOAD
        | {"origin_station_name": origin, "destination_station_name": destination},
    )

    assert response.status_code == 201
    booking = session.get(Booking, (response.json()["id"], DEPARTURE))
    assert booking is not None
    assert (booking.od.origin.code, booking.od.destination.code) == ("LPD", "MSC")


@pytest.mark.parametrize(
    "change",
    [
        {"service_number": "7851A"},
        {"departure_date": "2024-11-15"},
        {"origin_station_name": "ply", "destination_station_name": "adj"},
        # Both stops are served, but in the opposite direction.
        {"origin_station_name": "msc", "destination_station_name": "lpd"},
    ],
)
def test_unknown_od_is_rejected(client, session, plan, change):
    response = client.post("/bookings", json=PAYLOAD | change)

    assert response.status_code == 422
    assert "Unknown OD" in response.json()["detail"]
    assert session.query(Booking).count() == 0


def test_booking_is_rejected_until_cache_reload(client, session, redis):
    import_transport_plan(session, FILES)
    session.commit()
    assert client.post("/bookings", json=PAYLOAD).status_code == 422

    od_cache.reload(session, redis)
    assert client.post("/bookings", json=PAYLOAD).status_code == 201


@pytest.mark.parametrize(
    "change",
    [
        {"price": -1},
        {"price": 10.555},
        {"price": 10**8},
        {"currency": "eur"},
        {"departure_date": "30/05/2024"},
        {"destination_station_name": "lpd"},
        # Same station once uppercased.
        {"destination_station_name": "LPD"},
        {"cancel_datetime": "2024-04-27T00:00:00"},
        {"sale_datetime": "2024-04-28T04:55:27+02:00"},
        {"service_number": ""},
        {"unexpected": 1},
    ],
)
def test_invalid_payload_is_rejected(client, session, plan, change):
    response = client.post("/bookings", json=PAYLOAD | change)

    assert response.status_code == 422
    assert session.query(Booking).count() == 0


def test_missing_field_is_rejected(client, plan):
    payload = dict(PAYLOAD)
    del payload["sale_datetime"]

    assert client.post("/bookings", json=payload).status_code == 422


def post(client, payload, key=None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post("/bookings", json=payload, headers=headers)


def bookings(session):
    session.expire_all()
    return session.query(Booking).order_by(Booking.id).all()


def test_redelivery_without_key_returns_existing_booking(client, session, plan):
    first = post(client, PAYLOAD)
    again = post(client, PAYLOAD | {"origin_station_name": "LPD"})

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == first.json()
    assert len(bookings(session)) == 1


def test_different_sale_without_key_is_a_new_booking(client, session, plan):
    post(client, PAYLOAD)
    post(client, PAYLOAD | {"price": 11})
    post(client, PAYLOAD | {"sale_datetime": "2024-04-28T04:55:28"})

    assert len(bookings(session)) == 3


def test_redelivery_with_key_returns_existing_booking(client, session, plan):
    first = post(client, PAYLOAD, key="abc")
    again = post(client, PAYLOAD, key="abc")

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == first.json()
    assert bookings(session)[0].idempotency_key == "abc"


def test_identical_sales_with_different_keys_are_distinct(client, session, plan):
    # Two seats sold together: same fields, one key each.
    post(client, PAYLOAD, key="seat-1")
    post(client, PAYLOAD, key="seat-2")
    post(client, PAYLOAD)

    assert len(bookings(session)) == 3


def test_key_reused_for_another_sale_is_rejected(client, session, plan):
    post(client, PAYLOAD, key="abc")
    response = post(client, PAYLOAD | {"price": 11}, key="abc")

    assert response.status_code == 409
    assert [b.price for b in bookings(session)] == [Decimal(10)]


@pytest.mark.parametrize("key", [None, "abc"])
def test_cancellation_updates_existing_booking(client, session, plan, key):
    first = post(client, PAYLOAD, key=key)
    cancel = post(client, PAYLOAD | CANCEL, key=key)

    assert cancel.status_code == 200
    assert cancel.json() == first.json()
    [booking] = bookings(session)
    assert booking.cancel_datetime == datetime(2024, 4, 29, 10)


def test_late_sale_redelivery_does_not_undo_cancellation(client, session, plan):
    post(client, PAYLOAD | CANCEL)
    assert post(client, PAYLOAD).status_code == 200

    [booking] = bookings(session)
    assert booking.cancel_datetime == datetime(2024, 4, 29, 10)


def test_conflicting_cancellation_is_rejected(client, session, plan):
    post(client, PAYLOAD | CANCEL)
    response = post(client, PAYLOAD | {"cancel_datetime": "2024-04-30T10:00:00"})

    assert response.status_code == 409
    [booking] = bookings(session)
    assert booking.cancel_datetime == datetime(2024, 4, 29, 10)


@pytest.mark.parametrize("key", ["", "x" * 256])
def test_invalid_idempotency_key_is_rejected(client, session, plan, key):
    response = client.post("/bookings", json=PAYLOAD, headers={"Idempotency-Key": key})

    assert response.status_code == 422
    assert bookings(session) == []


@pytest.mark.parametrize("key", [None, "abc"])
def test_concurrent_redeliveries_store_one_booking(client, session, plan, key):
    headers = {"Idempotency-Key": key} if key else {}

    async def deliver_ten_times():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await asyncio.gather(
                *(c.post("/bookings", json=PAYLOAD, headers=headers) for _ in range(10))
            )

    responses = asyncio.run(deliver_ten_times())

    assert sorted(r.status_code for r in responses) == [200] * 9 + [201]
    assert len({r.json()["id"] for r in responses}) == 1
    assert len(bookings(session)) == 1
