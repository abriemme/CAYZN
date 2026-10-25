"""Transport plan (services, stops, ODs, current prices) and bookings.

These ORM models are deliberately separate from the API's Pydantic schemas.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cayzn.db import Base


class Stop(Base):
    """Served location, identified by its code (e.g. `PLY` for Paris Gare de Lyon).

    Codes are stored in uppercase; sources may send them in any case.
    """

    __tablename__ = "stops"
    __table_args__ = (CheckConstraint("code = upper(code)", name="code_uppercase"),)

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

    The service, date and stations of the payload are reachable through
    `service_id` and `od_id`; only the sale itself is stored here.

    A booking is identified by the sender's `Idempotency-Key` when there is one,
    otherwise by its natural key (OD, sale time, price, currency).
    """

    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("price >= 0", name="price_positive"),
        CheckConstraint(
            "cancel_datetime IS NULL OR cancel_datetime >= sale_datetime",
            name="cancel_after_sale",
        ),
        # Only for keyless bookings: two keyed bookings may share these fields
        # (e.g. two seats sold together).
        Index(
            "ix_bookings_natural_key",
            "od_id",
            "sale_datetime",
            "price",
            "currency",
            unique=True,
            postgresql_where=text("idempotency_key IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), index=True)
    od_id: Mapped[int] = mapped_column(ForeignKey("ods.id"), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3))
    sale_datetime: Mapped[datetime]
    cancel_datetime: Mapped[datetime | None]

    service: Mapped[Service] = relationship()
    od: Mapped[OriginDestination] = relationship()
