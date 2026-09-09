from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.auth import require_permission
from app.database import get_db
from app.email import send_lead_confirmation_email
from app.models import Lead, LeadAnswer, LeadContact, LeadNote, LeadStatus, LeadTask, User, UserRole
from app.schemas import (
    LeadCreate, LeadAssigneeOut, LeadContactOut, LeadNoteCreate, LeadNoteOut, LeadNoteUpdate, LeadOut, LeadUpdate,
    LeadTaskCreate, LeadTaskOut, LeadTaskUpdate,
)

router = APIRouter(prefix="/leads", tags=["leads"])

# Notes/tasks are lead sub-content, not their own resource — gated by the
# same "leads" permission as the lead they belong to (edit to add/update,
# view to just see the lead at all).
view_leads = require_permission("leads", "view")
edit_leads = require_permission("leads", "edit")
delete_leads = require_permission("leads", "delete")

CAN_ASSIGN = (UserRole.superadmin, UserRole.admin)


def _lead_address(answers: list[dict]) -> str | None:
    """A lead's address only shows up as a free-form questionnaire answer
    (e.g. "adresse_siege_social") — there's no dedicated Lead column for it."""
    for a in answers:
        if "adresse" in a["catalog_key"]:
            return a["value"]
    return None


def _visible(lead: Lead, user: User) -> bool:
    """A consultant only ever sees/acts on leads assigned to them — every
    other role that already passed the "leads" permission check sees
    everything, same as before assignment existed."""
    return user.role != UserRole.consultant or lead.assigned_to_id == user.id


def _lead_or_404(lead_id: int, db: Session, user: User) -> Lead:
    lead = db.get(Lead, lead_id)
    # 404, not 403, for a lead outside a consultant's assignment — same
    # "don't confirm it exists" reasoning as any other ownership check.
    if not lead or not _visible(lead, user):
        raise HTTPException(status_code=404, detail="Lead introuvable.")
    return lead


@router.post("/", response_model=LeadOut, status_code=201)
def create_lead(payload: LeadCreate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"answers"})
    # This endpoint is public (the website's own devis/contact forms post
    # here with no auth) — the only legitimate source of a non-null
    # assigned_to_id is the CRM's own "Nouveau lead" flow, so don't trust it
    # blindly from an arbitrary caller: silently drop anything that doesn't
    # resolve to a real, active account rather than erroring out a real
    # prospect's submission over it.
    if data.get("assigned_to_id") is not None:
        assignee = db.get(User, data["assigned_to_id"])
        if not assignee or not assignee.active:
            data["assigned_to_id"] = None
    lead = Lead(**data)
    answers = payload.model_dump()["answers"]
    lead.answers = [LeadAnswer(**a) for a in answers]
    db.add(lead)
    db.commit()
    db.refresh(lead)

    # A LeadContact snapshot, not a live relationship, so it outlives the
    # lead (or any edits to it) — see the LeadContact docstring in models.py.
    db.add(LeadContact(
        lead_id=lead.id, name=lead.name, phone=lead.phone,
        email=lead.email, address=_lead_address(answers),
    ))
    db.commit()

    if lead.email:
        send_lead_confirmation_email(lead.name, lead.email, lead.type.value)

    return lead


@router.get("/", response_model=list[LeadOut])
def list_leads(
    status: LeadStatus | None = Query(None),
    assigned_to_id: int | None = Query(None),
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    user=Depends(view_leads),
):
    q = db.query(Lead).filter(Lead.deleted.is_(False))
    if status:
        q = q.filter(Lead.status == status)
    if user.role == UserRole.consultant:
        q = q.filter(Lead.assigned_to_id == user.id)
    elif assigned_to_id is not None:
        q = q.filter(Lead.assigned_to_id == assigned_to_id)
    return q.order_by(Lead.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/contacts", response_model=list[LeadContactOut])
def list_lead_contacts(db: Session = Depends(get_db), _user=Depends(view_leads)):
    """Every contact ever collected from a lead — kept even after the source
    lead is deleted (see LeadContact in models.py)."""
    contacts = db.query(LeadContact).order_by(LeadContact.created_at.desc()).all()
    return [
        LeadContactOut(
            id=c.id, lead_id=c.lead_id, name=c.name, phone=c.phone,
            email=c.email, address=c.address, created_at=c.created_at,
            lead_deleted=c.lead is None or c.lead.deleted,
        )
        for c in contacts
    ]


@router.delete("/contacts/{contact_id}", status_code=204)
def delete_lead_contact(contact_id: int, db: Session = Depends(get_db), _user=Depends(delete_leads)):
    """Delete a collected-contact snapshot outright — unlike a lead itself,
    there's no soft-delete/undo for these, they're just a record of raw
    contact info someone submitted."""
    contact = db.query(LeadContact).filter(LeadContact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact introuvable.")
    db.delete(contact)
    db.commit()


@router.get("/assignable-users", response_model=list[LeadAssigneeOut])
def list_assignable_users(db: Session = Depends(get_db), user=Depends(view_leads)):
    """Who a lead can be handed to — the same set of people who are allowed
    to do the assigning themselves is a superset of "everyone assignable"
    in practice for a small team, but restrict the endpoint to superadmin/
    admin anyway since only they can act on what it returns."""
    if user.role not in CAN_ASSIGN:
        raise HTTPException(status_code=403, detail="Réservé aux administrateurs.")
    return db.query(User).filter(User.active.is_(True)).order_by(User.name.asc()).all()


@router.get("/{lead_id}", response_model=LeadOut)
def get_lead(lead_id: int, db: Session = Depends(get_db), user=Depends(view_leads)):
    return _lead_or_404(lead_id, db, user)


@router.patch("/{lead_id}", response_model=LeadOut)
def update_lead(lead_id: int, payload: LeadUpdate, db: Session = Depends(get_db), user=Depends(edit_leads)):
    lead = _lead_or_404(lead_id, db, user)
    updates = payload.model_dump(exclude_none=True, exclude={"assigned_to_id", "unassign"})

    if payload.unassign or payload.assigned_to_id is not None:
        if user.role not in CAN_ASSIGN:
            raise HTTPException(status_code=403, detail="Seuls les administrateurs peuvent réassigner un lead.")
        if payload.unassign:
            lead.assigned_to_id = None
        else:
            assignee = db.get(User, payload.assigned_to_id)
            if not assignee or not assignee.active:
                raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
            lead.assigned_to_id = assignee.id

    for field, value in updates.items():
        setattr(lead, field, value)

    # Keep the LeadContact snapshot (see models.py) in sync with whichever of
    # name/phone/email just changed, so it doesn't silently go stale.
    if {"name", "phone", "email"} & updates.keys():
        contact = db.query(LeadContact).filter(LeadContact.lead_id == lead_id).first()
        if contact:
            if "name" in updates:
                contact.name = updates["name"]
            if "phone" in updates:
                contact.phone = updates["phone"]
            if "email" in updates:
                contact.email = updates["email"]

    db.commit()
    db.refresh(lead)
    return lead


@router.delete("/{lead_id}", status_code=204)
def delete_lead(lead_id: int, db: Session = Depends(get_db), user=Depends(delete_leads)):
    """Soft delete: hides the lead from list_leads but keeps it (and its
    answers) in the DB — nothing is ever permanently lost from here."""
    lead = _lead_or_404(lead_id, db, user)
    lead.deleted = True
    db.commit()


# ── Sticky notes ──────────────────────────────────────────────────────────────

@router.post("/{lead_id}/notes", response_model=LeadNoteOut, status_code=201)
def create_note(lead_id: int, payload: LeadNoteCreate, db: Session = Depends(get_db), user=Depends(edit_leads)):
    lead = _lead_or_404(lead_id, db, user)
    note = LeadNote(lead_id=lead.id, **payload.model_dump())
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.patch("/notes/{note_id}", response_model=LeadNoteOut)
def update_note(note_id: int, payload: LeadNoteUpdate, db: Session = Depends(get_db), user=Depends(edit_leads)):
    note = db.get(LeadNote, note_id)
    if not note or not _visible(note.lead, user):
        raise HTTPException(status_code=404, detail="Note introuvable.")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(note, field, value)
    db.commit()
    db.refresh(note)
    return note


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(note_id: int, db: Session = Depends(get_db), user=Depends(edit_leads)):
    note = db.get(LeadNote, note_id)
    if not note or not _visible(note.lead, user):
        raise HTTPException(status_code=404, detail="Note introuvable.")
    db.delete(note)
    db.commit()


# ── Tasks ─────────────────────────────────────────────────────────────────────

@router.post("/{lead_id}/tasks", response_model=LeadTaskOut, status_code=201)
def create_task(lead_id: int, payload: LeadTaskCreate, db: Session = Depends(get_db), user=Depends(edit_leads)):
    lead = _lead_or_404(lead_id, db, user)
    task = LeadTask(lead_id=lead.id, **payload.model_dump())
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.patch("/tasks/{task_id}", response_model=LeadTaskOut)
def update_task(task_id: int, payload: LeadTaskUpdate, db: Session = Depends(get_db), user=Depends(edit_leads)):
    task = db.get(LeadTask, task_id)
    if not task or not _visible(task.lead, user):
        raise HTTPException(status_code=404, detail="Tâche introuvable.")
    # A completed task is locked (the CRM greys it out and disables its
    # fields) — enforced here too so the rule holds even against a direct
    # API call, not just the disabled inputs in the UI.
    if task.completed:
        raise HTTPException(status_code=409, detail="Cette tâche est terminée et ne peut plus être modifiée.")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(task, field, value)
    db.commit()
    db.refresh(task)
    return task


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db), user=Depends(edit_leads)):
    task = db.get(LeadTask, task_id)
    if not task or not _visible(task.lead, user):
        raise HTTPException(status_code=404, detail="Tâche introuvable.")
    db.delete(task)
    db.commit()
