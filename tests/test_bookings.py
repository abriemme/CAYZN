from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from cayzn import od_cache
from cayzn.importer import import_transport_plan
from cayzn.models import Booking

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


@pytest.fixture
def plan(session, redis):
    import_transport_plan(session, FILES)
    session.commit()
    od_cache.reload(session, redis)


def test_booking_is_stored_and_linked(client, session, plan):
    response = client.post(
        "/bookings", json=PAYLOAD | {"cancel_datetime": "2024-04-29T10:00:00"}
    )

    assert response.status_code == 201
    booking = session.get(Booking, response.json()["id"])
    assert booking is not None
    assert (booking.service_name, booking.origin, booking.destination) == (
        "9410",
        "LPD",
        "MSC",
    )
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
    booking = session.get(Booking, response.json()["id"])
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
