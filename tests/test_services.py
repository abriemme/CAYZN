"""Service listing: revenue, booking count, minimum price, date filters and pagination."""

from datetime import date

import pytest
from sqlalchemy import select
from test_bookings import PAYLOAD

from cayzn.models import Service


def names(response):
    return [(s["departure_date"], s["name"]) for s in response.json()["items"]]


def test_revenue_count_and_min_price(client, session, plan):
    client.post("/bookings", json=PAYLOAD | {"price": 10})
    client.post(
        "/bookings",
        json=PAYLOAD | {"price": 15.5, "sale_datetime": "2024-04-28T05:00:00"},
    )
    client.post(
        "/bookings",
        json=PAYLOAD
        | {
            "price": 99,
            "cancel_datetime": "2024-04-29T10:00:00",
            "sale_datetime": "2024-04-28T06:00:00",
        },
    )

    response = client.get("/services", params={"departure_date_from": "2024-11-14"})

    assert response.status_code == 200
    page = response.json()
    assert page["next_cursor"] is None
    assert len(page["items"]) == 5
    assert page["items"][0] == {
        "id": session.scalar(select(Service.id).where(Service.name == "9410")),
        "name": "9410",
        "departure_date": "2024-11-14",
        "revenue": "25.50",
        "booking_count": 2,
        "min_price": "20.00",
    }
    # No OD, no booking: still listed.
    assert page["items"][-1] == {
        "id": session.scalar(select(Service.id).where(Service.name == "5008")),
        "name": "5008",
        "departure_date": "2024-11-18",
        "revenue": "0",
        "booking_count": 0,
        "min_price": None,
    }


def test_defaults_to_today(client, session, plan):
    session.add(Service(name="FUTURE", departure_date=date.today()))
    session.add(Service(name="PAST", departure_date=date(2000, 1, 1)))
    session.commit()

    assert names(client.get("/services")) == [(date.today().isoformat(), "FUTURE")]


def test_date_range_is_inclusive(client, session, plan):
    response = client.get(
        "/services",
        params={"departure_date_from": "2024-11-15", "departure_date_to": "2024-11-16"},
    )
    assert names(response) == [("2024-11-15", "7866"), ("2024-11-16", "9464")]


def test_pagination_by_cursor(client, session, plan):
    # Same date as 9410: the cursor must not skip or repeat it.
    session.add(Service(name="9999", departure_date=date(2024, 11, 14)))
    session.commit()
    params = {"departure_date_from": "2024-11-14", "limit": 2}

    page1 = client.get("/services", params=params)
    assert names(page1) == [("2024-11-14", "9410"), ("2024-11-14", "9999")]
    page2 = client.get(
        "/services", params=params | {"cursor": page1.json()["next_cursor"]}
    )
    assert names(page2) == [("2024-11-15", "7866"), ("2024-11-16", "9464")]
    page3 = client.get(
        "/services", params=params | {"cursor": page2.json()["next_cursor"]}
    )
    assert names(page3) == [("2024-11-17", "7899"), ("2024-11-18", "5008")]
    assert page3.json()["next_cursor"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"departure_date_from": "2024-11-15", "departure_date_to": "2024-11-14"},
        {"cursor": "not-a-cursor"},
        {"cursor": "bm90LWEtZGF0ZXx4"},  # "not-a-date|x"
        {"limit": 0},
        {"limit": 101},
    ],
)
def test_invalid_params_are_rejected(client, plan, params):
    assert client.get("/services", params=params).status_code == 422
