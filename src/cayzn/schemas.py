"""API Pydantic schemas, separate from the ORM models."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, NaiveDatetime, model_validator

Code = Annotated[str, Field(min_length=1, max_length=16)]


class BookingIn(BaseModel):
    """Booking webhook payload."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    service_number: Annotated[str, Field(min_length=1, max_length=64)]
    departure_date: date
    origin_station_name: Code
    destination_station_name: Code
    # Numeric(10, 2) in the database.
    price: Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]
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
