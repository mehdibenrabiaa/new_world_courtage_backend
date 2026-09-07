from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Lead, LeadAnswer, LeadContact, LeadNote, LeadStatus, LeadTask
from app.schemas import (
    LeadCreate, LeadContactOut, LeadNoteCreate, LeadNoteOut, LeadNoteUpdate, LeadOut, LeadUpdate,
    LeadTaskCreate, LeadTaskOut, LeadTaskUpdate,
)

router = APIRouter(prefix="/leads", tags=["leads"])


def _lead_address(answers: list[dict]) -> str | None:
    """A lead's address only shows up as a free-form questionnaire answer
    (e.g. "adresse_siege_social") — there's no dedicated Lead column for it."""
    for a in answers:
        if "adresse" in a["catalog_key"]:
            return a["value"]
    return None


@router.post("/", response_model=LeadOut, status_code=201)
def create_lead(payload: LeadCreate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"answers"})
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
    return lead


@router.get("/", response_model=list[LeadOut])
def list_leads(
    status: LeadStatus | None = Query(None),
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    q = db.query(Lead).filter(Lead.deleted.is_(False))
    if status:
        q = q.filter(Lead.status == status)
    return q.order_by(Lead.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/contacts", response_model=list[LeadContactOut])
def list_lead_contacts(db: Session = Depends(get_db)):
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


@router.get("/{lead_id}", response_model=LeadOut)
def get_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead introuvable.")
    return lead


@router.patch("/{lead_id}", response_model=LeadOut)
def update_lead(lead_id: int, payload: LeadUpdate, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead introuvable.")
    updates = payload.model_dump(exclude_none=True)
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
def delete_lead(lead_id: int, db: Session = Depends(get_db)):
    """Soft delete: hides the lead from list_leads but keeps it (and its
    answers) in the DB — nothing is ever permanently lost from here."""
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead introuvable.")
    lead.deleted = True
    db.commit()


# ── Sticky notes ──────────────────────────────────────────────────────────────

@router.post("/{lead_id}/notes", response_model=LeadNoteOut, status_code=201)
def create_note(lead_id: int, payload: LeadNoteCreate, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead introuvable.")
    note = LeadNote(lead_id=lead_id, **payload.model_dump())
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.patch("/notes/{note_id}", response_model=LeadNoteOut)
def update_note(note_id: int, payload: LeadNoteUpdate, db: Session = Depends(get_db)):
    note = db.get(LeadNote, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note introuvable.")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(note, field, value)
    db.commit()
    db.refresh(note)
    return note


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(note_id: int, db: Session = Depends(get_db)):
    note = db.get(LeadNote, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note introuvable.")
    db.delete(note)
    db.commit()


# ── Tasks ─────────────────────────────────────────────────────────────────────

@router.post("/{lead_id}/tasks", response_model=LeadTaskOut, status_code=201)
def create_task(lead_id: int, payload: LeadTaskCreate, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead introuvable.")
    task = LeadTask(lead_id=lead_id, **payload.model_dump())
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.patch("/tasks/{task_id}", response_model=LeadTaskOut)
def update_task(task_id: int, payload: LeadTaskUpdate, db: Session = Depends(get_db)):
    task = db.get(LeadTask, task_id)
    if not task:
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
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(LeadTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tâche introuvable.")
    db.delete(task)
    db.commit()
