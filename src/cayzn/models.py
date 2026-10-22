"""Plan de transport en base : services, arrêts desservis, OD et prix courants.

Ces modèles ORM sont volontairement distincts des schémas Pydantic de l'API.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cayzn.db import Base


class Stop(Base):
    """Lieu desservi, identifié par son code (ex. `ply` pour Paris Gare de Lyon)."""

    __tablename__ = "stops"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)


class Service(Base):
    """Opération de transport (train, bus, vol), unique par nom et date de départ."""

    __tablename__ = "services"
    __table_args__ = (UniqueConstraint("name", "departure_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    departure_date: Mapped[date]

    stops: Mapped[list[ServiceStop]] = relationship(
        back_populates="service",
        order_by="ServiceStop.position",
        cascade="all, delete-orphan",
    )
    ods: Mapped[list[OriginDestination]] = relationship(
        back_populates="service", cascade="all, delete-orphan"
    )


class ServiceStop(Base):
    """Arrêt d'un service, à sa position dans l'itinéraire (0 = origine)."""

    __tablename__ = "service_stops"
    __table_args__ = (UniqueConstraint("service_id", "stop_id"),)

    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(primary_key=True)
    stop_id: Mapped[int] = mapped_column(ForeignKey("stops.id"))

    service: Mapped[Service] = relationship(back_populates="stops")
    stop: Mapped[Stop] = relationship()


class OriginDestination(Base):
    """Trajet réservable entre deux arrêts d'un service, avec son prix courant."""

    __tablename__ = "ods"
    __table_args__ = (
        UniqueConstraint("service_id", "origin_id", "destination_id"),
        CheckConstraint("origin_id <> destination_id", name="distinct_stops"),
        CheckConstraint("price >= 0", name="price_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE")
    )
    origin_id: Mapped[int] = mapped_column(ForeignKey("stops.id"))
    destination_id: Mapped[int] = mapped_column(ForeignKey("stops.id"))
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    service: Mapped[Service] = relationship(back_populates="ods")
    origin: Mapped[Stop] = relationship(foreign_keys=[origin_id])
    destination: Mapped[Stop] = relationship(foreign_keys=[destination_id])
