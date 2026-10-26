"""API Pydantic schemas, separate from the ORM models."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NaiveDatetime,
    StringConstraints,
    model_validator,
)

# Station codes are stored in uppercase: normalise whatever case the sender uses.
Code = Annotated[str, StringConstraints(min_length=1, max_length=16, to_upper=True)]
# Numeric(10, 2) in the database.
Price = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]


class BookingIn(BaseModel):
    """Booking webhook payload."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    service_number: Annotated[str, Field(min_length=1, max_length=64)]
    departure_date: date
    origin_station_name: Code
    destination_station_name: Code
    price: Price
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    # Local time of the reservation system, without timezone (as in the database).
    sale_datetime: NaiveDatetime
    cancel_datetime: NaiveDatetime | None = None

    @model_validator(mode="after")
    def check_consistency(self) -> Self:
        if self.origin_station_name == self.destination_station_name:
            raise ValueError("origin and destination must differ")
        if self.cancel_datetime and self.cancel_datetime < self.sale_datetime:
            raise ValueError("cancel_datetime must not precede sale_datetime")
        return self


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    service_id: int
    od_id: int


class Freshness(BaseModel):
    """When a service's forecast and optimization were last computed, and
    whether they still reflect its bookings. All null without a run."""

    forecast_computed_at: datetime | None
    optimization_computed_at: datetime | None
    # Sales or cancellations arrived since the last forecast (or no forecast yet).
    stale: bool
    # A forecast and optimization job is waiting to run.
    pending: bool


class ODOut(BaseModel):
    id: int
    origin: str
    destination: str
    price: Decimal | None
    # From the last optimization; null without one or without a current price.
    recommended_price: Decimal | None


class GridOut(BaseModel):
    service_id: int
    # Run of the recommended prices, to send back when applying one.
    forecast_id: int | None
    ods: list[ODOut]
    freshness: Freshness


class PriceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price: Price
    # Set to apply that run's recommendation: `price` must be it.
    forecast_id: int | None = None


class PriceOut(BaseModel):
    od: ODOut
    freshness: Freshness


class PriceChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    price: Decimal
    previous_price: Decimal | None
    changed_at: datetime
    source: str
    forecast_id: int | None


class CurvePoint(BaseModel):
    day_x: int
    bookings: float


class LoadingCurveOut(BaseModel):
    """Booking curve observed so far, then the last forecast for the day-x
    values not yet observed. Both run from the earliest day-x down to day-0."""

    service_id: int
    od_id: int | None
    observed: list[CurvePoint]
    forecast: list[CurvePoint]
    freshness: Freshness


class ServiceOut(BaseModel):
    """One row of the analysts' service listing."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    departure_date: date
    # Sum of the prices of bookings not cancelled.
    # ponytail: currencies are summed as one; split by currency if an operator
    # ever sells in several.
    revenue: Decimal
    booking_count: int
    # Lowest OD current price; None when no OD of the service has a price.
    min_price: Decimal | None


class ServicesPage(BaseModel):
    items: list[ServiceOut]
    # Opaque cursor for the next page, None on the last one.
    next_cursor: str | None
