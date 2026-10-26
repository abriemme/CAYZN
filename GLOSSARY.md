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
The price of an OD, as last received with the Transport plan or set by an analyst.
_Avoid_: Fare, tariff

**Price change**:
A change of an OD's Current price, by the nightly import or by an analyst, who may be applying a Recommended price. Kept as history.
_Avoid_: Price update, override

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
The day-x of a Service's most recent Booking sale or Cancellation. Day-x values from it down to day-0 are not yet observed.
_Avoid_: Today

**Booking curve**:
The cumulative Bookings, net of Cancellations, at each day-x, for a Service or one of its ODs.
_Avoid_: Load curve, booking profile

**Forecast**:
The expected Booking curve at every day-x not yet observed, for a Service and for each of its ODs.
_Avoid_: Prediction, projection

**Optimization**:
The step that turns a Forecast and the Current prices into a Recommended price for each OD of a Service.
_Avoid_: Pricing run

**Recommended price**:
The price Optimization suggests for an OD, for the analyst to act on.
_Avoid_: Optimal price, suggested fare

**Run**:
One Forecast of a Service followed by its Optimization, requested daily and after each Price change. A Run is _stale_ once Bookings or Cancellations arrive after it, and _pending_ while requested but not yet computed.
_Avoid_: Job, computation, batch

**Loading curve**:
A Service's or OD's Booking curve as observed so far, completed by its latest Forecast for the Day-x values not yet observed.
_Avoid_: Load curve, sales curve
