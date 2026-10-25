import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from cayzn.importer import TransportPlanError, import_transport_plan
from cayzn.models import OriginDestination, Service, ServiceStop, Stop

FILES = Path(__file__).parents[1] / "files"


@pytest.fixture
def plan(tmp_path):
    for f in FILES.glob("*.csv"):
        shutil.copy(f, tmp_path)
    return tmp_path


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def itinerary(session, name):
    service = session.scalars(select(Service).where(Service.name == name)).one()
    return [s.stop.code for s in service.stops]


def test_import_sample_files(session):
    import_transport_plan(session, FILES)
    session.commit()

    assert count(session, Service) == 5
    assert count(session, Stop) == 5
    assert itinerary(session, "9410") == ["PLY", "LPD", "MSC"]
    od = session.scalars(
        select(OriginDestination)
        .join(OriginDestination.service)
        .where(Service.name == "9410", OriginDestination.price == Decimal(20))
    ).one()
    assert (od.origin.code, od.destination.code) == ("PLY", "MSC")


def test_reimport_is_idempotent(session):
    for _ in range(2):
        import_transport_plan(session, FILES)
        session.commit()

    assert count(session, Service) == 5
    assert count(session, ServiceStop) == 7
    assert count(session, OriginDestination) == 5


def test_reimport_updates_itinerary_and_price(session, plan):
    import_transport_plan(session, plan)
    session.commit()

    (plan / "stops.csv").write_text(
        "departure_date,name,stops\n2024-11-15,7866,ply\n2024-11-15,7866,lpd\n"
        "2024-11-15,7866,msc\n"
    )
    (plan / "ods.csv").write_text(
        "departure_date,name,origin,destination\n2024-11-15,7866,ply,msc\n"
    )
    (plan / "prices.csv").write_text(
        "departure_date,name,origin,destination,price\n2024-11-15,7866,ply,msc,55.5\n"
    )
    import_transport_plan(session, plan)
    session.commit()

    assert itinerary(session, "7866") == ["PLY", "LPD", "MSC"]
    assert itinerary(session, "9410") == ["PLY", "LPD", "MSC"]
    prices = session.scalars(
        select(OriginDestination.price)
        .join(OriginDestination.service)
        .where(Service.name == "7866")
    ).all()
    assert prices == [Decimal("55.50")]


def test_station_codes_are_stored_uppercase(session, plan):
    (plan / "stops.csv").write_text(
        "departure_date,name,stops\n2024-11-14,9410,Ply\n2024-11-14,9410,lpd\n"
        "2024-11-14,9410,MSC\n"
    )
    (plan / "ods.csv").write_text(
        "departure_date,name,origin,destination\n2024-11-14,9410,pLy,Msc\n"
    )
    (plan / "prices.csv").write_text(
        "departure_date,name,origin,destination,price\n2024-11-14,9410,PLY,msc,20\n"
    )
    import_transport_plan(session, plan)
    session.commit()

    assert set(session.scalars(select(Stop.code))) == {"PLY", "LPD", "MSC"}
    assert itinerary(session, "9410") == ["PLY", "LPD", "MSC"]
    od = session.scalars(select(OriginDestination)).one()
    assert (od.origin.code, od.destination.code, od.price) == ("PLY", "MSC", 20)


def test_duplicate_stop_in_another_case_is_rejected(session, plan):
    (plan / "stops.csv").write_text(
        "departure_date,name,stops\n2024-11-14,9410,ply\n2024-11-14,9410,PLY\n"
    )
    with pytest.raises(TransportPlanError, match="duplicate stop PLY"):
        import_transport_plan(session, plan)


@pytest.mark.parametrize(
    ("file", "content", "error"),
    [
        ("trains.csv", "departure_date,name\n14/11/2024,9410\n", "invalid date"),
        ("trains.csv", "departure_date\n2024-11-14\n", "missing columns"),
        (
            "stops.csv",
            "departure_date,name,stops\n2024-12-01,1,ply\n",
            "missing from trains",
        ),
        (
            "ods.csv",
            "departure_date,name,origin,destination\n2024-11-14,9410,msc,ply\n",
            "does not come before",
        ),
        (
            "ods.csv",
            "departure_date,name,origin,destination\n2024-11-14,9410,ply,adj\n",
            "not served",
        ),
        (
            "prices.csv",
            "departure_date,name,origin,destination,price\n2024-11-14,9410,ply,lpd,-1\n",
            "invalid price",
        ),
        (
            "prices.csv",
            "departure_date,name,origin,destination,price\n2024-11-14,9410,ply,lpd,55.555\n",
            "invalid price",
        ),
        (
            "prices.csv",
            "departure_date,name,origin,destination,price\n2024-11-14,9410,ply,lpd,1e8\n",
            "invalid price",
        ),
        (
            "prices.csv",
            "departure_date,name,origin,destination,price\n2024-11-14,9410,ply,lpd,\n",
            "empty value",
        ),
    ],
)
def test_invalid_rows_are_rejected(session, plan, file, content, error):
    (plan / file).write_text(content)
    with pytest.raises(TransportPlanError, match=error):
        import_transport_plan(session, plan)
