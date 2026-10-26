"""Transport plan (services, stops, ODs, current prices), bookings, and the
forecasts and recommended prices computed from them, and their queue.

These ORM models are deliberately separate from the API's Pydantic schemas.

Bookings are range-partitioned by month of `departure_date` (Postgres declarative
partitioning): revenue management reads them by departure date, so a query only
scans the months it needs, and a past month is archived by dropping its partition.
Postgres requires the partition key in the primary key and every unique
constraint. The nightly import creates the partitions (`importer.ensure_partitions`).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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

    The service and stations of the payload are reachable through `service_id`
    and `od_id`; only the sale itself is stored here, plus the service's
    `departure_date` as the partition key.

    A booking is identified by the sender's `Idempotency-Key` when there is one,
    otherwise by its natural key (OD, sale time, price, currency). Both are
    unique per departure date: Postgres cannot enforce uniqueness across
    partitions, so the same key sent for two departure dates stores two bookings.
    """

    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("price >= 0", name="price_positive"),
        CheckConstraint(
            "cancel_datetime IS NULL OR cancel_datetime >= sale_datetime",
            name="cancel_after_sale",
        ),
        UniqueConstraint("idempotency_key", "departure_date"),
        # Only for keyless bookings: two keyed bookings may share these fields
        # (e.g. two seats sold together).
        Index(
            "ix_bookings_natural_key",
            "od_id",
            "sale_datetime",
            "price",
            "currency",
            "departure_date",
            unique=True,
            postgresql_where=text("idempotency_key IS NULL"),
        ),
        {"postgresql_partition_by": "RANGE (departure_date)"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    departure_date: Mapped[date] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), index=True)
    od_id: Mapped[int] = mapped_column(ForeignKey("ods.id"), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3))
    sale_datetime: Mapped[datetime]
    cancel_datetime: Mapped[datetime | None]

    service: Mapped[Service] = relationship()
    od: Mapped[OriginDestination] = relationship()


class Forecast(Base):
    """Forecast of a service, kept for every run (append-only).

    `curves` maps "service" and each OD id to {day-x: expected bookings},
    from `current_day_x` down to day-0.
    """

    __tablename__ = "forecasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), index=True
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    current_day_x: Mapped[int]
    curves: Mapped[dict[str, Any]] = mapped_column(JSONB)


class RecommendedPrice(Base):
    """Price recommended for an OD by the optimization of a forecast."""

    __tablename__ = "recommended_prices"
    __table_args__ = (CheckConstraint("price >= 0", name="price_positive"),)

    forecast_id: Mapped[int] = mapped_column(
        ForeignKey("forecasts.id", ondelete="CASCADE"), primary_key=True
    )
    od_id: Mapped[int] = mapped_column(
        ForeignKey("ods.id", ondelete="CASCADE"), primary_key=True
    )
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))


class PendingForecast(Base):
    """Service waiting for its forecast and optimization job (the worker's queue).

    Removed when a worker takes it: a booking arriving meanwhile queues it again.
    """

    __tablename__ = "pending_forecasts"

    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), primary_key=True
    )
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
