from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from cayzn import revenue_management
from cayzn.revenue_management import Curves, observed_curve, optimize

DEPARTURE = date(2024, 11, 14)


def forecast(*args):
    result = revenue_management.forecast(*args)
    assert result is not None
    return result


def at(day_x):
    return datetime(2024, 11, 14, 12) - timedelta(days=day_x)


def test_forecast_extends_each_od_at_its_pace():
    bookings = [
        # OD 1: one sale a day from day-10 to day-8.
        (1, at(10), None),
        (1, at(9), None),
        (1, at(8), None),
        # OD 2: sold then cancelled.
        (2, at(9), at(8)),
    ]

    current, curves = forecast(DEPARTURE, [1, 2, 3], bookings)

    assert current == 8
    assert curves["1"] == {d: 3 + (8 - d) for d in range(8, -1, -1)}
    assert curves["2"] == curves["3"] == {d: 0 for d in range(8, -1, -1)}
    assert curves["service"] == curves["1"]


def test_forecast_pace_uses_last_seven_day_x():
    # Ten sales at day-20, then one a day from day-7 to day-1.
    bookings = [(1, at(20), None)] * 10 + [(1, at(d), None) for d in range(7, 0, -1)]

    current, curves = forecast(DEPARTURE, [1], bookings)

    assert current == 1
    assert curves["1"] == {1: 17, 0: 18}


def test_cancellations_lower_the_pace_but_not_below_zero():
    bookings = [(1, at(10), None)] + [(1, at(10), at(3))] * 13 + [(1, at(2), None)]

    current, curves = forecast(DEPARTURE, [1], bookings)

    assert current == 2
    # Pace over day-9..day-2: from 14 to 2 bookings.
    assert curves["1"] == {2: 2, 1: pytest.approx(2 - 12 / 7), 0: 0}


def test_cancellation_after_the_last_sale_is_observed():
    current, curves = forecast(DEPARTURE, [1], [(1, at(5), None), (1, at(5), at(3))])

    assert current == 3
    assert curves["1"][3] == 1


def test_observed_curve_counts_cancellations_when_cancelled():
    bookings = [(1, at(10), None), (1, at(9), at(7)), (2, at(8), None)]

    assert observed_curve(DEPARTURE, bookings) == {10: 1, 9: 2, 8: 3, 7: 2}
    assert observed_curve(DEPARTURE, bookings, od_id=1) == {10: 1, 9: 2, 8: 2, 7: 1}
    assert observed_curve(DEPARTURE, bookings, od_id=3) == {10: 0, 9: 0, 8: 0, 7: 0}
    assert observed_curve(DEPARTURE, []) == {}


def test_nothing_to_forecast_after_departure():
    assert revenue_management.forecast(DEPARTURE, [1], [(1, at(-1), None)]) is None
    assert revenue_management.forecast(DEPARTURE, [1], []) is None


def test_optimize_moves_prices_with_relative_growth():
    curves: Curves = {
        "1": {2: 2, 0: 6},  # x3, faster than the service
        "2": {2: 2, 0: 2},  # x1, slower
        "3": {2: 0, 0: 0},  # no bookings: no signal
        "4": {2: 2, 0: 2},
        "service": {2: 6, 0: 10},
    }
    prices = {1: Decimal(40), 2: Decimal("20.05"), 3: Decimal(50), 4: None}

    assert optimize(2, curves, prices) == {
        1: Decimal("44.00"),
        2: Decimal("18.05"),
        3: Decimal("50.00"),
    }


def test_optimize_keeps_prices_growing_like_the_service():
    curves: Curves = {"1": {1: 3, 0: 6}, "service": {1: 3, 0: 6}}

    assert optimize(1, curves, {1: Decimal(40)}) == {1: Decimal("40.00")}
