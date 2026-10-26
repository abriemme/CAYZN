"""Changing an OD's price, keeping its history and its denormalised current price in step."""

from decimal import Decimal

from cayzn.models import OriginDestination, PriceChange


def change_price(
    od: OriginDestination, price: Decimal, source: str, forecast_id: int | None = None
) -> PriceChange | None:
    """Set the OD's price and return the history row to add; None if unchanged.

    `forecast_id` records that the analyst applied that run's recommendation.
    """
    price = price.quantize(Decimal("0.01"))  # as stored, for the response
    if od.price == price:
        return None
    change = PriceChange(
        od=od,
        price=price,
        previous_price=od.price,
        source=source,
        forecast_id=forecast_id,
    )
    od.price = price
    return change
