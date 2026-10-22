# CAYZN

Revenue management for passenger transport: CAYZN receives an operator's transport plan and its bookings, then forecasts demand and recommends prices.

## Transport plan

**Transport plan**:
The set of Services, their Itineraries, ODs and Current prices, as sent nightly by the reservation system.

**Service**:
A passenger transport operation (train, bus, flight) identified by its name and departure date.
_Avoid_: Train, trip, run

**Stop**:
A location served by a Service, identified by its code (e.g. `PLY` for Paris Gare de Lyon).
_Avoid_: Station

**Itinerary**:
The ordered list of Stops a Service serves.
_Avoid_: Route, path

**OD** (origin-destination):
A trip a passenger can book on a Service, from one of its Stops to a later one.
_Avoid_: Leg, segment, trip

**Current price**:
The price of an OD as set in the reservation system and received with the Transport plan.
_Avoid_: Fare, tariff

## Bookings

**Booking**:
One sale of an OD at a given price and time, received from the reservation system.
_Avoid_: Reservation, ticket, sale

**Cancellation**:
The undoing of a Booking, recorded as the time it was cancelled on that Booking.
_Avoid_: Refund

## Revenue management

**Day-x**:
The number of days before a Service's departure; day-0 is the departure day. Revenue management reasons in day-x rather than dates.
_Avoid_: J-x, days-to-departure, DTD

**Current day-x**:
The day-x of a Service's most recent Booking sale. Day-x values from it down to day-0 are not yet observed.
_Avoid_: Today

**Forecast**:
The expected cumulative Bookings at every day-x not yet observed, for a Service and for each of its ODs.
_Avoid_: Prediction, projection

**Optimization**:
The step that turns a Forecast and the Current prices into a Recommended price for each OD of a Service.
_Avoid_: Pricing run

**Recommended price**:
The price Optimization suggests for an OD, for the analyst to act on.
_Avoid_: Optimal price, suggested fare
