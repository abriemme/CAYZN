"""Transport plan (services, stops, ODs, current prices) and bookings.

These ORM models are deliberately separate from the API's Pydantic schemas.
"""

from datetime import date, datetime
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
    """Served location, identified by its code (e.g. `ply` for Paris Gare de Lyon)."""

    __tablename__ = "stops"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)


class Service(Base):
    """Transport operation (train, bus, flight), unique by name and departure date."""

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
    """Stop of a service, at its position in the itinerary (0 = origin)."""

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
    """Bookable trip between two stops of a service, with its current price."""

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


class Booking(Base):
    """Booking received by webhook, linked to its OD in the transport plan.

    The raw payload fields are kept as received alongside the resolved links.
    """

    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("origin <> destination", name="distinct_stops"),
        CheckConstraint("price >= 0", name="price_positive"),
        CheckConstraint(
            "cancel_datetime IS NULL OR cancel_datetime >= sale_datetime",
            name="cancel_after_sale",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_name: Mapped[str] = mapped_column(String(64))
    departure_date: Mapped[date]
    origin: Mapped[str] = mapped_column(String(16))
    destination: Mapped[str] = mapped_column(String(16))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3))
    sale_datetime: Mapped[datetime]
    cancel_datetime: Mapped[datetime | None]
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), index=True)
    od_id: Mapped[int] = mapped_column(ForeignKey("ods.id"), index=True)

    service: Mapped[Service] = relationship()
    od: Mapped[OriginDestination] = relationship()
