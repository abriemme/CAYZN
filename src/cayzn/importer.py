"""Nightly import of the transport plan from the reservation system's CSV files.

Usage: python -m cayzn.importer <directory>  (trains.csv, stops.csv, ods.csv, prices.csv)

The import is idempotent: re-running it on the same files changes nothing.
Everything runs in a single transaction: one invalid row aborts the whole import.
"""

import csv
import sys
from collections.abc import Iterator
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from cayzn import od_cache
from cayzn.db import SessionLocal
from cayzn.models import OriginDestination, Service, ServiceStop, Stop
from cayzn.prices import change_price
from cayzn.settings import settings

ServiceKey = tuple[str, date]

# Station codes are stored in uppercase, whatever the case in the CSV files.
_STATION_COLUMNS = {"stops", "origin", "destination"}


class TransportPlanError(ValueError):
    pass


def _rows(path: Path, columns: set[str]) -> Iterator[tuple[str, dict[str, str]]]:
    """CSV rows with their `file:line` location, for error messages."""
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = columns - set(reader.fieldnames or [])
        if missing:
            raise TransportPlanError(f"{path.name}: missing columns {sorted(missing)}")
        for row in reader:
            where = f"{path.name}:{reader.line_num}"
            if any(not (row[c] or "").strip() for c in columns):
                raise TransportPlanError(f"{where}: empty value")
            values = {c: row[c].strip() for c in columns}
            for c in columns & _STATION_COLUMNS:
                values[c] = values[c].upper()
            yield where, values


def _service_key(where: str, row: dict[str, str]) -> ServiceKey:
    try:
        return row["name"], date.fromisoformat(row["departure_date"])
    except ValueError:
        raise TransportPlanError(
            f"{where}: invalid date {row['departure_date']!r}"
        ) from None


def import_transport_plan(session: Session, directory: Path) -> None:
    keys = [
        _service_key(where, row)
        for where, row in _rows(directory / "trains.csv", {"departure_date", "name"})
    ]

    # Preload existing services to avoid one query per row.
    existing = session.scalars(
        select(Service)
        .where(Service.departure_date.in_({d for _, d in keys}))
        .options(selectinload(Service.stops), selectinload(Service.ods))
    )
    services: dict[ServiceKey, Service] = {
        (s.name, s.departure_date): s for s in existing
    }
    for name, departure_date in keys:
        if (name, departure_date) not in services:
            services[name, departure_date] = Service(
                name=name, departure_date=departure_date
            )
            session.add(services[name, departure_date])
    services = {key: services[key] for key in keys}

    stops = {s.code: s for s in session.scalars(select(Stop))}

    def stop(code: str) -> Stop:
        if code not in stops:
            stops[code] = Stop(code=code)
            session.add(stops[code])
        return stops[code]

    def service(where: str, row: dict[str, str]) -> Service:
        key = _service_key(where, row)
        if key not in services:
            raise TransportPlanError(
                f"{where}: service {key[0]} on {key[1]} is missing from trains.csv"
            )
        return services[key]

    # Itineraries: the row order in stops.csv is the order the stops are served.
    itineraries: dict[ServiceKey, list[str]] = {}
    for where, row in _rows(
        directory / "stops.csv", {"departure_date", "name", "stops"}
    ):
        codes = itineraries.setdefault(_service_key(where, row), [])
        service(where, row)
        if row["stops"] in codes:
            raise TransportPlanError(f"{where}: duplicate stop {row['stops']}")
        codes.append(row["stops"])

    for key, codes in itineraries.items():
        svc = services[key]
        if [s.stop.code for s in svc.stops] == codes:
            continue
        svc.stops.clear()
        session.flush()  # delete the old itinerary before reinserting the same keys
        svc.stops.extend(
            ServiceStop(position=i, stop=stop(c)) for i, c in enumerate(codes)
        )

    def od_key(od: OriginDestination) -> tuple[str, str]:
        return od.origin.code, od.destination.code

    ods = {(id(svc), *od_key(od)): od for svc in services.values() for od in svc.ods}

    def find_od(
        where: str, row: dict[str, str]
    ) -> tuple[Service, OriginDestination | None]:
        svc = service(where, row)
        return svc, ods.get((id(svc), row["origin"], row["destination"]))

    for where, row in _rows(
        directory / "ods.csv", {"departure_date", "name", "origin", "destination"}
    ):
        svc, od = find_od(where, row)
        if od:
            continue
        positions = {s.stop.code: s.position for s in svc.stops}
        o, d = row["origin"], row["destination"]
        if o not in positions or d not in positions:
            raise TransportPlanError(
                f"{where}: {o}-{d} is not served by service {svc.name}"
            )
        if positions[o] >= positions[d]:
            raise TransportPlanError(
                f"{where}: {o} does not come before {d} in the itinerary"
            )
        od = OriginDestination(origin=stop(o), destination=stop(d))
        svc.ods.append(od)
        ods[(id(svc), o, d)] = od

    for where, row in _rows(
        directory / "prices.csv",
        {"departure_date", "name", "origin", "destination", "price"},
    ):
        _, od = find_od(where, row)
        if od is None:
            raise TransportPlanError(
                f"{where}: OD {row['origin']}-{row['destination']} is missing from ods.csv"
            )
        try:
            price = Decimal(row["price"])
        except InvalidOperation:
            raise TransportPlanError(
                f"{where}: invalid price {row['price']!r}"
            ) from None
        # Numeric(10, 2): at most 8 digits before the decimal point and 2 after.
        if (
            not price.is_finite()
            or not 0 <= price < 10**8
            or price != price.quantize(Decimal("0.01"))
        ):
            raise TransportPlanError(f"{where}: invalid price {row['price']!r}")
        if change := change_price(od, price, "import"):
            session.add(change)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m cayzn.importer <directory>")
    with SessionLocal.begin() as session:
        import_transport_plan(session, Path(sys.argv[1]))
    # After the commit: the API resolves bookings against the new plan.
    with SessionLocal() as session, Redis.from_url(str(settings.redis_url)) as redis:
        print(f"{od_cache.reload(session, redis)} ODs loaded into the cache")
