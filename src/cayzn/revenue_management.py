"""Forecast and Optimization heuristics, placeholders for the real algorithms.

A Forecast extends each OD's booking curve linearly, at its pace over the last
days; the service's curve is the sum of its ODs'. Optimization raises the price
of ODs expected to grow faster than their service, and lowers the others.
"""

import math
from collections.abc import Iterable
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

# Curves by "service" or OD id (as str, like JSON keys), then by day-x.
type Curves = dict[str, dict[int, float]]

PACE_WINDOW = 7  # day-x
UP, DOWN = Decimal("1.10"), Decimal("0.90")
CENT = Decimal("0.01")


def day_x(departure_date: date, moment: datetime) -> int:
    return (departure_date - moment.date()).days


def forecast(
    departure_date: date,
    od_ids: Iterable[int],
    bookings: Iterable[tuple[int, datetime, datetime | None]],
) -> tuple[int, Curves] | None:
    """Current day-x and expected curves from it down to day-0, from the
    service's (od_id, sale, cancellation) bookings; None if nothing is left
    to forecast."""
    sold = [
        (
            od_id,
            day_x(departure_date, sale),
            None if cancel is None else day_x(departure_date, cancel),
        )
        for od_id, sale, cancel in bookings
    ]
    if not sold:
        return None
    # Latest sale or cancellation: both are observed.
    current = min(
        min(sale, sale if cancel is None else cancel) for _, sale, cancel in sold
    )
    if current < 0:  # sold after departure
        return None
    window = min(PACE_WINDOW, max(sale for _, sale, _ in sold) - current + 1)

    def booking_curve(od_id: int, d: int) -> int:
        return sum(
            (sale >= d) - (cancel is not None and cancel >= d)
            for o, sale, cancel in sold
            if o == od_id
        )

    curves: Curves = {}
    for od_id in od_ids:
        now = booking_curve(od_id, current)
        pace = (now - booking_curve(od_id, current + window)) / window
        curves[str(od_id)] = {
            d: max(0.0, now + pace * (current - d)) for d in range(current, -1, -1)
        }
    curves["service"] = {
        d: sum(curve[d] for curve in curves.values()) for d in range(current, -1, -1)
    }
    return current, curves


def optimize(
    current_day_x: int, curves: Curves, prices: dict[int, Decimal | None]
) -> dict[int, Decimal]:
    """Recommended price of each OD with a current price."""

    def growth(curve: dict[int, float]) -> float | None:
        now = curve[current_day_x]
        return curve[0] / now if now else None

    service = growth(curves["service"])
    recommended = {}
    for od_id, price in prices.items():
        if price is None:
            continue
        od = growth(curves[str(od_id)])
        if od is None or service is None or math.isclose(od, service):
            factor = Decimal(1)
        else:
            factor = UP if od > service else DOWN
        recommended[od_id] = (price * factor).quantize(CENT, ROUND_HALF_UP)
    return recommended
