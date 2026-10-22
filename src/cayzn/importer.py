"""Import nocturne du plan de transport depuis les CSV du système de réservation.

Usage : python -m cayzn.importer <dossier>  (trains.csv, stops.csv, ods.csv, prices.csv)

L'import est idempotent : relancé sur les mêmes fichiers, il ne change rien.
Tout est fait dans une seule transaction : une ligne invalide annule l'import entier.
"""

import csv
import sys
from collections.abc import Iterator
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from cayzn.db import SessionLocal
from cayzn.models import OriginDestination, Service, ServiceStop, Stop

ServiceKey = tuple[str, date]


class TransportPlanError(ValueError):
    pass


def _rows(path: Path, columns: set[str]) -> Iterator[tuple[str, dict[str, str]]]:
    """Lignes du CSV avec leur emplacement `fichier:ligne` pour les messages d'erreur."""
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = columns - set(reader.fieldnames or [])
        if missing:
            raise TransportPlanError(
                f"{path.name}: colonnes manquantes {sorted(missing)}"
            )
        for row in reader:
            where = f"{path.name}:{reader.line_num}"
            if any(not (row[c] or "").strip() for c in columns):
                raise TransportPlanError(f"{where}: valeur vide")
            yield where, {c: row[c].strip() for c in columns}


def _service_key(where: str, row: dict[str, str]) -> ServiceKey:
    try:
        return row["name"], date.fromisoformat(row["departure_date"])
    except ValueError:
        raise TransportPlanError(
            f"{where}: date invalide {row['departure_date']!r}"
        ) from None


def import_transport_plan(session: Session, directory: Path) -> None:
    keys = [
        _service_key(where, row)
        for where, row in _rows(directory / "trains.csv", {"departure_date", "name"})
    ]

    # Précharge l'existant pour éviter une requête par ligne.
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
                f"{where}: service {key[0]} du {key[1]} absent de trains.csv"
            )
        return services[key]

    # Itinéraires : l'ordre des lignes de stops.csv donne l'ordre de desserte.
    itineraries: dict[ServiceKey, list[str]] = {}
    for where, row in _rows(
        directory / "stops.csv", {"departure_date", "name", "stops"}
    ):
        codes = itineraries.setdefault(_service_key(where, row), [])
        service(where, row)
        if row["stops"] in codes:
            raise TransportPlanError(f"{where}: arrêt {row['stops']} en double")
        codes.append(row["stops"])

    for key, codes in itineraries.items():
        svc = services[key]
        if [s.stop.code for s in svc.stops] == codes:
            continue
        svc.stops.clear()
        session.flush()  # supprime l'ancien itinéraire avant de réinsérer les mêmes clés
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
                f"{where}: {o}-{d} n'est pas desservi par le service {svc.name}"
            )
        if positions[o] >= positions[d]:
            raise TransportPlanError(
                f"{where}: {o} n'est pas avant {d} dans l'itinéraire"
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
                f"{where}: OD {row['origin']}-{row['destination']} absente de ods.csv"
            )
        try:
            price = Decimal(row["price"])
        except InvalidOperation:
            raise TransportPlanError(
                f"{where}: prix invalide {row['price']!r}"
            ) from None
        if not price.is_finite() or price < 0:
            raise TransportPlanError(f"{where}: prix invalide {row['price']!r}")
        od.price = price


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m cayzn.importer <dossier>")
    with SessionLocal.begin() as session:
        import_transport_plan(session, Path(sys.argv[1]))
