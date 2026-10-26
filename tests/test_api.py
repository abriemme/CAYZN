"""Grid, price changes and loading curves, with their freshness block."""

from datetime import date, timedelta

from sqlalchemy import select
from test_bookings import CANCEL, PAYLOAD, post
from test_worker import pending, request

from cayzn.models import OriginDestination, PriceChange, Service

NO_RUN = {
    "forecast_computed_at": None,
    "optimization_computed_at": None,
    "stale": False,
    "pending": False,
}

# Sale at day-200 on LPD-MSC, as in PAYLOAD.
PLY_LPD = PAYLOAD | {
    "origin_station_name": "ply",
    "destination_station_name": "lpd",
    "sale_datetime": "2024-04-20T08:00:00",  # day-208
}


def od_id(session, origin, destination):
    return session.scalar(
        select(OriginDestination.id)
        .join(OriginDestination.service)
        .where(
            Service.name == "9410",
            OriginDestination.origin.has(code=origin),
            OriginDestination.destination.has(code=destination),
        )
    )


def run(session, run_worker):
    request(session, 1)
    run_worker()


def test_grid_without_run_has_null_freshness(client, session, plan):
    grid = client.get("/services/1/ods").json()

    assert grid["forecast_id"] is None
    assert grid["freshness"] == NO_RUN
    assert [(od["origin"], od["destination"], od["price"]) for od in grid["ods"]] == [
        ("PLY", "LPD", "40.00"),
        ("PLY", "MSC", "20.00"),
        ("LPD", "MSC", "50.00"),
    ]
    assert all(od["recommended_price"] is None for od in grid["ods"])
    assert client.get("/services/999/ods").status_code == 404


def test_grid_shows_recommended_prices_and_freshness(client, session, plan, run_worker):
    post(client, PAYLOAD)
    post(client, PLY_LPD)
    grid = client.get("/services/1/ods").json()
    assert grid["freshness"] == NO_RUN | {"stale": True}  # bookings, no run yet

    run(session, run_worker)
    grid = client.get("/services/1/ods").json()
    assert grid["forecast_id"] == 1
    assert grid["freshness"]["forecast_computed_at"] is not None
    assert grid["freshness"]["optimization_computed_at"] is not None
    assert grid["freshness"] | {"stale": False, "pending": False} == grid["freshness"]
    assert {od["id"]: od["recommended_price"] for od in grid["ods"]} == {
        od_id(session, "PLY", "LPD"): "36.00",
        od_id(session, "PLY", "MSC"): "20.00",
        od_id(session, "LPD", "MSC"): "55.00",
    }

    # A cancellation counts as a new event: the run is stale until requested.
    post(client, PAYLOAD | CANCEL)
    assert client.get("/services/1/ods").json()["freshness"]["stale"] is True
    request(session, 1)
    assert client.get("/services/1/ods").json()["freshness"]["pending"] is True


def test_change_price_keeps_history_and_requests_a_run(client, session, plan):
    od = od_id(session, "PLY", "LPD")

    response = client.put(f"/ods/{od}/price", json={"price": 42})
    assert response.status_code == 200
    assert response.json()["od"] == {
        "id": od,
        "origin": "PLY",
        "destination": "LPD",
        "price": "42.00",
        "recommended_price": None,
    }
    assert response.json()["freshness"] == NO_RUN | {"pending": True}
    assert pending(session) == [1]
    session.expire_all()
    assert session.get(OriginDestination, od).price == 42

    history = client.get(f"/ods/{od}/price-history").json()
    assert [(h["previous_price"], h["price"], h["source"]) for h in history] == [
        ("40.00", "42.00", "analyst"),
        (None, "40.00", "import"),
    ]
    assert history[0]["forecast_id"] is None
    assert history[0]["changed_at"] is not None

    # Same price again: nothing recorded.
    assert client.put(f"/ods/{od}/price", json={"price": 42}).status_code == 200
    assert session.query(PriceChange).filter_by(od_id=od).count() == 2


def test_apply_recommended_price(client, session, plan, run_worker):
    post(client, PAYLOAD)
    post(client, PLY_LPD)
    run(session, run_worker)
    od = od_id(session, "LPD", "MSC")

    wrong = client.put(f"/ods/{od}/price", json={"price": 54, "forecast_id": 1})
    assert wrong.status_code == 409
    assert (
        client.put(f"/ods/{od}/price", json={"price": 55, "forecast_id": 2}).status_code
        == 409
    )

    response = client.put(f"/ods/{od}/price", json={"price": 55, "forecast_id": 1})
    assert response.status_code == 200
    assert response.json()["od"]["price"] == "55.00"
    assert response.json()["od"]["recommended_price"] == "55.00"
    assert response.json()["freshness"]["pending"] is True
    assert client.get(f"/ods/{od}/price-history").json()[0]["forecast_id"] == 1


def test_invalid_price_changes_are_rejected(client, session, plan):
    assert client.put("/ods/1/price", json={"price": -1}).status_code == 422
    assert client.put("/ods/1/price", json={"price": 1.234}).status_code == 422
    assert client.put("/ods/1/price", json={}).status_code == 422
    assert client.put("/ods/999/price", json={"price": 1}).status_code == 404
    assert client.get("/ods/999/price-history").status_code == 404
    assert pending(session) == []


def test_loading_curve_without_bookings_is_empty(client, plan):
    curve = client.get("/services/1/loading-curve").json()

    assert curve == {
        "service_id": 1,
        "od_id": None,
        "observed": [],
        "forecast": [],
        "freshness": NO_RUN,
    }
    assert client.get("/services/999/loading-curve").status_code == 404
    assert client.get("/services/1/loading-curve?od_id=999").status_code == 404
    # OD of another service.
    assert client.get("/services/2/loading-curve?od_id=1").status_code == 404


def test_loading_curve_completes_observed_with_forecast(
    client, session, plan, run_worker
):
    post(client, PAYLOAD)
    post(client, PLY_LPD)
    run(session, run_worker)

    curve = client.get("/services/1/loading-curve").json()
    observed = {p["day_x"]: p["bookings"] for p in curve["observed"]}
    assert observed == {d: 1 for d in range(208, 200, -1)} | {200: 2}
    forecast = {p["day_x"]: p["bookings"] for p in curve["forecast"]}
    assert set(forecast) == set(range(199, -1, -1))
    assert forecast[199] > 2  # the service keeps selling
    assert curve["freshness"]["stale"] is False

    lpd_msc = client.get(
        f"/services/1/loading-curve?od_id={od_id(session, 'LPD', 'MSC')}"
    ).json()
    assert {p["day_x"]: p["bookings"] for p in lpd_msc["observed"]} == {
        d: 0 for d in range(208, 200, -1)
    } | {200: 1}
    assert lpd_msc["forecast"][0] == {"day_x": 199, "bookings": 1 + 1 / 7}

    # Bookings since the run are observed, and replace its forecast there.
    post(client, PAYLOAD | {"sale_datetime": "2024-04-30T04:55:27"})  # day-198
    curve = client.get("/services/1/loading-curve").json()
    assert curve["observed"][-1] == {"day_x": 198, "bookings": 3}
    assert curve["forecast"][0]["day_x"] == 197
    assert curve["freshness"]["stale"] is True


def test_cancellation_lowers_the_curve_when_cancelled(client, session, plan):
    post(client, PAYLOAD | {"cancel_datetime": "2024-05-05T10:00:00"})  # day-193
    post(client, PLY_LPD)

    curve = client.get("/services/1/loading-curve").json()
    observed = {p["day_x"]: p["bookings"] for p in curve["observed"]}
    assert observed[200] == 2
    assert observed[194] == 2
    assert observed[193] == 1
    assert min(observed) == 193


def test_service_departed_has_no_run(client, session, plan, run_worker):
    session.add(Service(name="past", departure_date=date.today() - timedelta(days=1)))
    session.commit()
    post(client, PAYLOAD | {"sale_datetime": "2024-11-20T00:00:00"})  # after departure
    run(session, run_worker)

    assert client.get("/services/1/ods").json()["freshness"] == NO_RUN | {"stale": True}
