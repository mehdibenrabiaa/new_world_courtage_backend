from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.auth import get_current_user, require_permission
from app.database import get_db
from app.email import send_booking_confirmation_email
from app.models import ConsultantBooking, ConsultantUnavailability, Lead, LeadActivity, RolePermission, User, UserRole
from app.schemas import (
    AvailabilityOut, BookingCreate, BookingOut, ConsultantBookingOut, ConsultantBookingReallocate,
    ConsultantBookingWithConsultantOut, ConsultantRef, ConsultantUnavailabilityCreate, ConsultantUnavailabilityOut,
    SlotOut, UserOut,
)

router = APIRouter(prefix="/consultants", tags=["consultants"])

# Mirrors the public site's own SLOTS list (CarInsuranceForm.js) — the fixed
# set of callback times a prospect can be offered in a day.
SLOTS = ["09:00", "10:00", "11:00", "14:00", "15:00", "16:00", "17:00"]

view_consultants = require_permission("consultants", "view")
edit_consultants = require_permission("consultants", "edit")


def _has_permission(db: Session, user: User, action: str) -> bool:
    """Same check require_permission's dependency makes, just as a plain
    boolean instead of a 403-or-pass dependency — used inside _can_manage
    below, which also needs the "OR it's your own calendar" branch that a
    single Depends() can't express."""
    if user.role == UserRole.superadmin:
        return True
    row = db.query(RolePermission).filter(
        RolePermission.role == user.role, RolePermission.resource == "consultants", RolePermission.action == action,
    ).first()
    return bool(row and row.allowed)


def _can_manage(target_user_id: int, user: User, db: Session, action: str = "edit") -> bool:
    """Whoever the "consultants" permission matrix grants `action` to, OR
    it's your own calendar — the self-service half is deliberately NOT
    part of the configurable matrix, since it's about acting on your own
    account, not a role-based grant. A "consultant" is just a User with
    that role now (no separate profile/account to link), so this is a
    plain id comparison."""
    return _has_permission(db, user, action) or target_user_id == user.id


def _user_or_404(user_id: int, db: Session) -> User:
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Consultant introuvable.")
    return target


def _log_lead_activity(db: Session, lead_id: int | None, actor_id: int | None, action: str, description: str) -> None:
    """A small local copy of routers/leads.py's _log_activity — kept
    separate rather than imported to avoid a cross-router dependency for
    one helper; both just append a LeadActivity row for the caller's own
    commit to pick up."""
    if lead_id is None:
        return
    db.add(LeadActivity(lead_id=lead_id, actor_id=actor_id, action=action, description=description))


@router.get("/availability", response_model=AvailabilityOut)
def get_availability(date: str = Query(...), db: Session = Depends(get_db)):
    """A slot is offered only if at least one active, role=consultant user
    is both not already booked and hasn't blocked that day/slot themselves."""
    total_active = db.query(User).filter(User.role == UserRole.consultant, User.active.is_(True)).count()
    if total_active == 0:
        return AvailabilityOut(date=date, slots=[SlotOut(time=s, available=False) for s in SLOTS])

    booked_counts = dict(
        db.query(ConsultantBooking.time, func.count(ConsultantBooking.id))
        .join(User, User.id == ConsultantBooking.consultant_id)
        .filter(ConsultantBooking.date == date, User.role == UserRole.consultant, User.active.is_(True))
        .group_by(ConsultantBooking.time)
        .all()
    )

    whole_day_blocked_ids = {
        row[0] for row in db.query(ConsultantUnavailability.consultant_id)
        .join(User, User.id == ConsultantUnavailability.consultant_id)
        .filter(
            ConsultantUnavailability.date == date,
            ConsultantUnavailability.time.is_(None),
            User.role == UserRole.consultant, User.active.is_(True),
        )
        .all()
    }
    effectively_active = total_active - len(whole_day_blocked_ids)
    if effectively_active <= 0:
        return AvailabilityOut(date=date, slots=[SlotOut(time=s, available=False) for s in SLOTS])

    slot_blocked_query = (
        db.query(ConsultantUnavailability.time, func.count(ConsultantUnavailability.id))
        .join(User, User.id == ConsultantUnavailability.consultant_id)
        .filter(
            ConsultantUnavailability.date == date,
            ConsultantUnavailability.time.isnot(None),
            User.role == UserRole.consultant, User.active.is_(True),
        )
    )
    if whole_day_blocked_ids:
        slot_blocked_query = slot_blocked_query.filter(ConsultantUnavailability.consultant_id.notin_(whole_day_blocked_ids))
    slot_blocked_counts = dict(slot_blocked_query.group_by(ConsultantUnavailability.time).all())

    slots = [
        SlotOut(time=s, available=(booked_counts.get(s, 0) + slot_blocked_counts.get(s, 0)) < effectively_active)
        for s in SLOTS
    ]
    return AvailabilityOut(date=date, slots=slots)


@router.post("/book", response_model=BookingOut, status_code=201)
def book_slot(payload: BookingCreate, db: Session = Depends(get_db)):
    if payload.time not in SLOTS:
        raise HTTPException(status_code=400, detail="Créneau invalide.")

    active_consultants = db.query(User).filter(User.role == UserRole.consultant, User.active.is_(True)).all()
    if not active_consultants:
        raise HTTPException(status_code=409, detail="Aucun conseiller disponible.")

    already_booked_ids = {
        row[0] for row in db.query(ConsultantBooking.consultant_id).filter(
            ConsultantBooking.date == payload.date, ConsultantBooking.time == payload.time
        )
    }
    # A whole-day block (time IS NULL) or a block on this exact slot both
    # take a consultant out of the running — re-checked here (not just
    # trusted from the earlier /availability call) in case something changed
    # in between, same reasoning the already_booked_ids check above has
    # always had.
    blocked_ids = {
        row[0] for row in db.query(ConsultantUnavailability.consultant_id).filter(
            ConsultantUnavailability.date == payload.date,
            (ConsultantUnavailability.time.is_(None)) | (ConsultantUnavailability.time == payload.time),
        )
    }

    consultant = next(
        (c for c in active_consultants if c.id not in already_booked_ids and c.id not in blocked_ids), None
    )
    if not consultant:
        raise HTTPException(status_code=409, detail="Ce créneau vient d'être réservé, merci d'en choisir un autre.")

    booking = ConsultantBooking(
        consultant_id=consultant.id, lead_id=payload.lead_id, date=payload.date, time=payload.time,
    )
    db.add(booking)
    _log_lead_activity(
        db, payload.lead_id, actor_id=None, action="call_booked",
        description=f"Appel réservé avec {consultant.name} le {payload.date} à {payload.time}.",
    )
    db.commit()
    db.refresh(booking)

    lead = db.get(Lead, payload.lead_id) if payload.lead_id else None
    if lead and lead.email:
        send_booking_confirmation_email(lead.name, lead.email, booking.date, booking.time)

    return BookingOut(
        id=booking.id, date=booking.date, time=booking.time,
        consultant_name=consultant.name, created_at=booking.created_at,
    )


# ── CRM-only consultant management (auth required from here down) ──────────


@router.get("", response_model=list[UserOut])
def list_consultants(db: Session = Depends(get_db), user: User = Depends(view_consultants)):
    """Every role=consultant User — there's no separate consultant profile
    to manage here anymore; create/edit one from the Users page (name,
    email, active, role) same as any other account."""
    return db.query(User).filter(User.role == UserRole.consultant).order_by(User.name).all()


@router.get("/bookings", response_model=list[ConsultantBookingWithConsultantOut])
def list_all_bookings(
    month: str = Query(..., description="YYYY-MM"),
    consultant_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Backs the "Rendez-vous" sidebar section — whoever has the
    "consultants" view permission (admin/superadmin by default) sees every
    consultant's bookings, optionally narrowed to one via `consultant_id`;
    everyone else (a plain consultant) only ever sees their own, same
    self-service-only reasoning as _can_manage above, just applied to a
    cross-consultant list instead of one consultant's calendar."""
    q = db.query(ConsultantBooking).filter(ConsultantBooking.date.startswith(month))
    if _has_permission(db, user, "view"):
        if consultant_id is not None:
            q = q.filter(ConsultantBooking.consultant_id == consultant_id)
    else:
        q = q.filter(ConsultantBooking.consultant_id == user.id)

    bookings = q.order_by(ConsultantBooking.date, ConsultantBooking.time).all()
    lead_ids = {b.lead_id for b in bookings if b.lead_id is not None}
    lead_names = dict(db.query(Lead.id, Lead.name).filter(Lead.id.in_(lead_ids)).all()) if lead_ids else {}
    consultant_ids = {b.consultant_id for b in bookings}
    consultants = {c.id: c.name for c in db.query(User).filter(User.id.in_(consultant_ids)).all()} if consultant_ids else {}

    return [
        ConsultantBookingWithConsultantOut(
            id=b.id, date=b.date, time=b.time, lead_id=b.lead_id,
            lead_name=lead_names.get(b.lead_id), created_at=b.created_at,
            # The account behind an old booking can be gone by now — nothing
            # currently stops a consultant's User row from being deleted
            # while their historical bookings still reference it (no
            # ON DELETE on ConsultantBooking.consultant_id) — same
            # "supprimé" fallback wording ConsultantBookingOut already uses
            # for a booking whose lead was deleted.
            consultant=ConsultantRef(id=b.consultant_id, name=consultants.get(b.consultant_id, "Consultant supprimé")),
        )
        for b in bookings
    ]


@router.get("/{consultant_id}/unavailabilities", response_model=list[ConsultantUnavailabilityOut])
def list_unavailabilities(
    consultant_id: int, month: str = Query(..., description="YYYY-MM"),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    _user_or_404(consultant_id, db)
    if not _can_manage(consultant_id, user, db, "view"):
        raise HTTPException(status_code=403, detail="Vous ne pouvez consulter que votre propre calendrier.")
    return (
        db.query(ConsultantUnavailability)
        .filter(ConsultantUnavailability.consultant_id == consultant_id, ConsultantUnavailability.date.startswith(month))
        .order_by(ConsultantUnavailability.date, ConsultantUnavailability.time)
        .all()
    )


@router.get("/{consultant_id}/bookings", response_model=list[ConsultantBookingOut])
def list_consultant_bookings(
    consultant_id: int, month: str = Query(..., description="YYYY-MM"),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """The consultant's real, already-confirmed appointments for that
    month — shown alongside their deliberate blocks so their calendar
    reflects their actual schedule, not just what they've chosen to block."""
    _user_or_404(consultant_id, db)
    if not _can_manage(consultant_id, user, db, "view"):
        raise HTTPException(status_code=403, detail="Vous ne pouvez consulter que votre propre calendrier.")
    bookings = (
        db.query(ConsultantBooking)
        .filter(ConsultantBooking.consultant_id == consultant_id, ConsultantBooking.date.startswith(month))
        .order_by(ConsultantBooking.date, ConsultantBooking.time)
        .all()
    )
    lead_ids = {b.lead_id for b in bookings if b.lead_id is not None}
    lead_names = {}
    if lead_ids:
        lead_names = dict(db.query(Lead.id, Lead.name).filter(Lead.id.in_(lead_ids)).all())
    return [
        ConsultantBookingOut(
            id=b.id, date=b.date, time=b.time, lead_id=b.lead_id,
            lead_name=lead_names.get(b.lead_id), created_at=b.created_at,
        )
        for b in bookings
    ]


@router.post("/{consultant_id}/unavailabilities", response_model=ConsultantUnavailabilityOut, status_code=201)
def block_timeframe(
    consultant_id: int, payload: ConsultantUnavailabilityCreate,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    _user_or_404(consultant_id, db)
    if not _can_manage(consultant_id, user, db):
        raise HTTPException(status_code=403, detail="Vous ne pouvez modifier que votre propre calendrier.")
    if payload.time is not None and payload.time not in SLOTS:
        raise HTTPException(status_code=400, detail="Créneau invalide.")

    existing = db.query(ConsultantUnavailability).filter(
        ConsultantUnavailability.consultant_id == consultant_id,
        ConsultantUnavailability.date == payload.date,
        ConsultantUnavailability.time == payload.time,
    ).first()
    if existing:
        return existing

    if payload.time is None:
        # Blocking the whole day makes any of that day's per-slot blocks
        # redundant — collapse them into the one whole-day row instead of
        # leaving stale rows behind.
        db.query(ConsultantUnavailability).filter(
            ConsultantUnavailability.consultant_id == consultant_id,
            ConsultantUnavailability.date == payload.date,
            ConsultantUnavailability.time.isnot(None),
        ).delete()

    unavailability = ConsultantUnavailability(consultant_id=consultant_id, date=payload.date, time=payload.time)
    db.add(unavailability)
    db.commit()
    db.refresh(unavailability)
    return unavailability


@router.delete("/{consultant_id}/unavailabilities/{unavailability_id}", status_code=204)
def unblock_timeframe(
    consultant_id: int, unavailability_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    _user_or_404(consultant_id, db)
    if not _can_manage(consultant_id, user, db):
        raise HTTPException(status_code=403, detail="Vous ne pouvez modifier que votre propre calendrier.")
    unavailability = db.get(ConsultantUnavailability, unavailability_id)
    if not unavailability or unavailability.consultant_id != consultant_id:
        raise HTTPException(status_code=404, detail="Blocage introuvable.")
    db.delete(unavailability)
    db.commit()


@router.patch("/{consultant_id}/bookings/{booking_id}/reallocate", response_model=ConsultantBookingOut)
def reallocate_booking(
    consultant_id: int, booking_id: int, payload: ConsultantBookingReallocate,
    db: Session = Depends(get_db), user: User = Depends(edit_consultants),
):
    """Moves a confirmed appointment to a different consultant — e.g. one
    of them is out sick and their existing calls need a home. Manager-only
    (via the "consultants" permission, not self-service): unlike blocking
    your own calendar, this also affects a second consultant's schedule."""
    booking = db.get(ConsultantBooking, booking_id)
    if not booking or booking.consultant_id != consultant_id:
        raise HTTPException(status_code=404, detail="Rendez-vous introuvable.")

    new_consultant = db.get(User, payload.new_consultant_id)
    if not new_consultant or new_consultant.role != UserRole.consultant or not new_consultant.active:
        raise HTTPException(status_code=404, detail="Consultant introuvable ou inactif.")
    if new_consultant.id == consultant_id:
        raise HTTPException(status_code=400, detail="Ce rendez-vous est déjà avec ce consultant.")

    already_booked = db.query(ConsultantBooking).filter(
        ConsultantBooking.consultant_id == new_consultant.id,
        ConsultantBooking.date == booking.date, ConsultantBooking.time == booking.time,
    ).first()
    if already_booked:
        raise HTTPException(status_code=409, detail="Ce consultant a déjà un rendez-vous à ce créneau.")
    blocked = db.query(ConsultantUnavailability).filter(
        ConsultantUnavailability.consultant_id == new_consultant.id,
        ConsultantUnavailability.date == booking.date,
        (ConsultantUnavailability.time.is_(None)) | (ConsultantUnavailability.time == booking.time),
    ).first()
    if blocked:
        raise HTTPException(status_code=409, detail="Ce consultant a bloqué ce créneau.")

    old_consultant_name = db.get(User, consultant_id).name
    booking.consultant_id = new_consultant.id
    _log_lead_activity(
        db, booking.lead_id, actor_id=user.id, action="call_reallocated",
        description=f"Appel du {booking.date} à {booking.time} réaffecté de {old_consultant_name} à {new_consultant.name}.",
    )
    db.commit()
    db.refresh(booking)

    lead_name = db.get(Lead, booking.lead_id).name if booking.lead_id else None
    return ConsultantBookingOut(
        id=booking.id, date=booking.date, time=booking.time, lead_id=booking.lead_id,
        lead_name=lead_name, created_at=booking.created_at,
    )
