"""Recording bookings received from the reservation system."""

from sqlalchemy import and_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from cayzn.models import Booking
from cayzn.schemas import BookingIn


class BookingConflict(Exception):
    """The delivery contradicts the booking already received."""


async def record_booking(
    db: AsyncSession,
    payload: BookingIn,
    service_id: int,
    od_id: int,
    idempotency_key: str | None,
) -> tuple[int, bool]:
    """Store the booking, or apply its cancellation if already received.

    Returns the booking id and whether it was created.
    """
    sale = (od_id, payload.sale_datetime, payload.price, payload.currency)

    # The database arbitrates duplicates, including concurrent deliveries:
    # the insert is skipped when the booking already exists.
    insert_stmt = insert(Booking).values(
        departure_date=payload.departure_date,  # partition key
        idempotency_key=idempotency_key,
        service_id=service_id,
        od_id=od_id,
        price=payload.price,
        currency=payload.currency,
        sale_datetime=payload.sale_datetime,
        cancel_datetime=payload.cancel_datetime,
    )
    if idempotency_key is None:
        insert_stmt = insert_stmt.on_conflict_do_nothing(
            index_elements=[
                "od_id",
                "sale_datetime",
                "price",
                "currency",
                "departure_date",
            ],
            index_where=Booking.idempotency_key.is_(None),
        )
        match = and_(
            Booking.idempotency_key.is_(None),
            tuple_(
                Booking.od_id, Booking.sale_datetime, Booking.price, Booking.currency
            )
            == tuple_(*sale),
        )
    else:
        insert_stmt = insert_stmt.on_conflict_do_nothing(
            index_elements=["idempotency_key", "departure_date"]
        )
        match = Booking.idempotency_key == idempotency_key

    booking_id = await db.scalar(insert_stmt.returning(Booking.id))
    if booking_id is not None:
        await db.commit()
        return booking_id, True

    # Already received: lock it so concurrent cancellations apply one at a time.
    booking = (
        await db.scalars(
            select(Booking)
            .where(Booking.departure_date == payload.departure_date, match)
            .with_for_update()
        )
    ).one()
    if (
        booking.od_id,
        booking.sale_datetime,
        booking.price,
        booking.currency,
    ) != sale:
        raise BookingConflict("Idempotency-Key already used for a different booking")
    if payload.cancel_datetime is not None:
        if booking.cancel_datetime is None:
            booking.cancel_datetime = payload.cancel_datetime
        elif booking.cancel_datetime != payload.cancel_datetime:
            raise BookingConflict("Booking already cancelled at a different time")
    # Read before commit(): the commit expires the object's attributes.
    booking_id = booking.id
    await db.commit()
    return booking_id, False
