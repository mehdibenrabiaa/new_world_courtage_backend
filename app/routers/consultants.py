from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.database import get_db
from app.email import send_booking_confirmation_email
from app.models import Consultant, ConsultantBooking, Lead
from app.schemas import AvailabilityOut, BookingCreate, BookingOut, SlotOut

router = APIRouter(prefix="/consultants", tags=["consultants"])

# Mirrors the public site's own SLOTS list (CarInsuranceForm.js) — the fixed
# set of callback times a prospect can be offered in a day.
SLOTS = ["09:00", "10:00", "11:00", "14:00", "15:00", "16:00", "17:00"]


@router.get("/availability", response_model=AvailabilityOut)
def get_availability(date: str = Query(...), db: Session = Depends(get_db)):
    """A slot is offered only if at least one active consultant has no
    booking at that date/time yet."""
    total_active = db.query(Consultant).filter(Consultant.active.is_(True)).count()
    if total_active == 0:
        return AvailabilityOut(date=date, slots=[SlotOut(time=s, available=False) for s in SLOTS])

    booked_counts = dict(
        db.query(ConsultantBooking.time, func.count(ConsultantBooking.id))
        .join(Consultant, Consultant.id == ConsultantBooking.consultant_id)
        .filter(ConsultantBooking.date == date, Consultant.active.is_(True))
        .group_by(ConsultantBooking.time)
        .all()
    )
    slots = [SlotOut(time=s, available=booked_counts.get(s, 0) < total_active) for s in SLOTS]
    return AvailabilityOut(date=date, slots=slots)


@router.post("/book", response_model=BookingOut, status_code=201)
def book_slot(payload: BookingCreate, db: Session = Depends(get_db)):
    if payload.time not in SLOTS:
        raise HTTPException(status_code=400, detail="Créneau invalide.")

    active_consultants = db.query(Consultant).filter(Consultant.active.is_(True)).all()
    if not active_consultants:
        raise HTTPException(status_code=409, detail="Aucun conseiller disponible.")

    already_booked_ids = {
        row[0] for row in db.query(ConsultantBooking.consultant_id).filter(
            ConsultantBooking.date == payload.date, ConsultantBooking.time == payload.time
        )
    }
    consultant = next((c for c in active_consultants if c.id not in already_booked_ids), None)
    if not consultant:
        raise HTTPException(status_code=409, detail="Ce créneau vient d'être réservé, merci d'en choisir un autre.")

    booking = ConsultantBooking(
        consultant_id=consultant.id, lead_id=payload.lead_id, date=payload.date, time=payload.time,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)

    lead = db.get(Lead, payload.lead_id) if payload.lead_id else None
    if lead and lead.email:
        send_booking_confirmation_email(lead.name, lead.email, booking.date, booking.time)

    return BookingOut(
        id=booking.id, date=booking.date, time=booking.time,
        consultant_name=consultant.name, created_at=booking.created_at,
    )
