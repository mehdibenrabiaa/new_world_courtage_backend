import hashlib
import hmac
import re
import secrets
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import case, func
from sqlalchemy.orm import Session, joinedload
from app.auth import require_permission, require_superadmin
from app.config import settings
from app.database import get_db
from app.email import send_lead_confirmation_email
from app.models import (
    Contact, Lead, LeadActivity, LeadAnswer, LeadContact, LeadDocument, LeadNote, LeadStatus, LeadTask, LeadType,
    Notification, User, UserRole,
)
from app.schemas import (
    LeadCreate, LeadActivityOut, LeadAssigneeOut, LeadContactOut, LeadListOut, LeadNoteCreate, LeadNoteOut,
    LeadNoteUpdate, LeadOut, LeadStatsOut, ConsultantStatOut, LeadUpdate, LeadTaskCreate, LeadTaskOut, LeadTaskUpdate,
    LeadTaskWithLeadOut, TaskLeadRef, LeadDocumentOut,
)
from app.test_data import generate_fake_garage_lead

router = APIRouter(prefix="/leads", tags=["leads"])

# Notes/tasks are lead sub-content, not their own resource — gated by the
# same "leads" permission as the lead they belong to (edit to add/update,
# view to just see the lead at all).
view_leads = require_permission("leads", "view")
edit_leads = require_permission("leads", "edit")
delete_leads = require_permission("leads", "delete")

CAN_ASSIGN = (UserRole.superadmin, UserRole.admin)
LEAD_UPLOAD_ROOT = Path("uploads/leads")
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}


def _notify_assigned(db: Session, lead: Lead, assignee: User, actor: User | None) -> None:
    """Queue a notification for whoever a lead just got assigned to — not
    committed here, piggybacks on the caller's own db.commit() so the
    assignment and its notification land atomically. `actor` is who did the
    assigning (None for the public-site create flow, which has no logged-in
    user) — skipped when someone assigns a lead to themselves."""
    if actor is not None and actor.id == assignee.id:
        return
    message = (
        f"{actor.name} vous a assigné le lead « {lead.name} »." if actor
        else f"Un nouveau lead vous a été assigné : « {lead.name} »."
    )
    db.add(Notification(
        user_id=assignee.id,
        type="lead_assigned",
        message=message,
        link=f"/dashboard/leads/{lead.id}",
    ))


def _log_activity(
    db: Session, lead_id: int, actor: User | None, action: str,
    field: str | None = None, old_value: object = None, new_value: object = None,
    description: str | None = None,
) -> None:
    """Appends one entry to a lead's activity timeline — not committed here,
    piggybacks on the caller's own db.commit() same as _notify_assigned.
    Values are stringified (enums via .value) so the row stays a plain,
    always-renderable snapshot regardless of the field's real Python type."""
    def _stringify(v: object) -> str | None:
        if v is None:
            return None
        if hasattr(v, "value"):
            return str(v.value)
        return str(v)

    db.add(LeadActivity(
        lead_id=lead_id, actor_id=actor.id if actor else None, action=action,
        field=field, old_value=_stringify(old_value), new_value=_stringify(new_value),
        description=description,
    ))


def _find_duplicate(db: Session, lead: Lead) -> Lead | None:
    """The earliest other non-deleted lead sharing this phone or email, if
    any — used right after creating a new lead to flag (not block) likely
    resubmissions. Phone is the more reliable signal (always present);
    email is optional so it's only checked when set."""
    q = db.query(Lead).filter(Lead.id != lead.id, Lead.deleted.is_(False))
    if lead.email:
        q = q.filter((Lead.phone == lead.phone) | (Lead.email == lead.email))
    else:
        q = q.filter(Lead.phone == lead.phone)
    return q.order_by(Lead.created_at.asc()).first()


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
    assignee = None
    if data.get("assigned_to_id") is not None:
        assignee = db.get(User, data["assigned_to_id"])
        if not assignee or not assignee.active:
            assignee = None
            data["assigned_to_id"] = None
    upload_token = secrets.token_urlsafe(32)
    lead = Lead(
        **data,
        document_upload_token_hash=hashlib.sha256(upload_token.encode()).hexdigest(),
    )
    answers = payload.model_dump()["answers"]
    lead.answers = [LeadAnswer(**a) for a in answers]
    db.add(lead)
    db.commit()
    db.refresh(lead)

    _log_activity(db, lead.id, actor=None, action="created")
    duplicate = _find_duplicate(db, lead)
    if duplicate:
        lead.duplicate_of_id = duplicate.id
        _log_activity(
            db, lead.id, actor=None, action="duplicate_detected",
            description=f"Doublon possible de « {duplicate.name} » (#{duplicate.id}).",
        )
    db.commit()

    if assignee:
        _notify_assigned(db, lead, assignee, actor=None)
        db.commit()

    # A LeadContact snapshot, not a live relationship, so it outlives the
    # lead (or any edits to it) — see the LeadContact docstring in models.py.
    db.add(LeadContact(
        lead_id=lead.id, name=lead.name, phone=lead.phone,
        email=lead.email, address=_lead_address(answers),
    ))
    db.commit()

    if lead.email:
        send_lead_confirmation_email(lead.name, lead.email, lead.type.value)

    lead.document_upload_token = upload_token
    return lead


@router.post("/generate-test-data", status_code=201)
def generate_test_data(count: int = 5, db: Session = Depends(get_db), user=Depends(require_superadmin)):
    """Creates `count` fake "Assurance Garage" leads (realistic answers
    shaped exactly like a real garagiste/devis submission — see
    app/test_data.py) so the CRM's own views can be exercised without
    hand-filling the public form or the "Nouveau lead" dialog repeatedly.
    Superadmin-only: this writes real rows, same access level as user
    management, not one of the delegable "leads" permissions."""
    if not 1 <= count <= 50:
        raise HTTPException(status_code=400, detail="count doit être entre 1 et 50.")
    created_ids = []
    for _ in range(count):
        data = generate_fake_garage_lead()
        lead = Lead(
            type=LeadType.garage,
            name=data["name"],
            phone=data["phone"],
            email=data["email"],
            siret=data["siret"],
            activite=data["activite"],
            source="Données de test",
        )
        lead.answers = [LeadAnswer(**a) for a in data["answers"]]
        db.add(lead)
        db.flush()
        created_ids.append(lead.id)
    db.commit()
    return {"created": len(created_ids), "ids": created_ids}


@router.post("/{lead_id}/documents", response_model=LeadDocumentOut, status_code=201)
async def upload_lead_document(
    lead_id: int,
    file: UploadFile = File(...),
    document_label: str = Form(...),
    upload_token: str = Form(...),
    db: Session = Depends(get_db),
):
    lead = db.get(Lead, lead_id)
    if not lead or lead.deleted:
        raise HTTPException(status_code=404, detail="Lead introuvable.")

    token_hash = hashlib.sha256(upload_token.encode()).hexdigest()
    if not lead.document_upload_token_hash or not hmac.compare_digest(lead.document_upload_token_hash, token_hash):
        raise HTTPException(status_code=403, detail="Lien d'envoi invalide.")

    original_filename = Path(file.filename or "document").name
    extension = Path(original_filename).suffix.lower()
    if extension not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Format non pris en charge. Utilisez un PDF, JPG ou PNG.")

    contents = await file.read(MAX_DOCUMENT_BYTES + 1)
    if not contents:
        raise HTTPException(status_code=400, detail="Le fichier est vide.")
    if len(contents) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=400, detail="Le fichier ne doit pas dépasser 10 Mo.")

    safe_label = re.sub(r"\s+", " ", document_label).strip()[:200]
    if not safe_label:
        raise HTTPException(status_code=400, detail="Le type de document est requis.")

    lead_dir = LEAD_UPLOAD_ROOT / str(lead.id)
    lead_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{uuid.uuid4().hex}{extension}"
    destination = lead_dir / stored_filename
    destination.write_bytes(contents)

    document = LeadDocument(
        lead_id=lead.id,
        document_label=safe_label,
        original_filename=original_filename[:300],
        stored_filename=stored_filename,
        content_type=file.content_type,
        size_bytes=len(contents),
        file_url=f"{settings.base_url}/uploads/leads/{lead.id}/{stored_filename}",
    )
    db.add(document)
    _log_activity(db, lead.id, actor=None, action="document_uploaded", description=safe_label)
    db.commit()
    db.refresh(document)
    return document


@router.get("/{lead_id}/documents", response_model=list[LeadDocumentOut])
def list_lead_documents(lead_id: int, db: Session = Depends(get_db), user=Depends(view_leads)):
    lead = _lead_or_404(lead_id, db, user)
    return lead.documents


# The static /uploads mount serves the on-disk (random UUID) filename with no
# Content-Disposition header, so a plain link to file_url downloads as
# "83890d1a92fa4ea68b37f6507abd0bd9.pdf" — this route goes through the same
# view-leads permission check and hands back the file under its real,
# originally-uploaded name instead.
@router.get("/{lead_id}/documents/{document_id}/download")
def download_lead_document(lead_id: int, document_id: int, db: Session = Depends(get_db), user=Depends(view_leads)):
    lead = _lead_or_404(lead_id, db, user)
    document = next((d for d in lead.documents if d.id == document_id), None)
    if not document:
        raise HTTPException(status_code=404, detail="Document introuvable.")
    path = LEAD_UPLOAD_ROOT / str(lead_id) / document.stored_filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    return FileResponse(path, filename=document.original_filename, media_type=document.content_type or "application/octet-stream")




@router.get("/", response_model=list[LeadListOut])
def list_leads(
    response: Response,
    status: LeadStatus | None = Query(None),
    type: LeadType | None = Query(None),
    assigned_to_id: int | None = Query(None),
    unassigned: bool = Query(False),
    search: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
    user=Depends(view_leads),
):
    # LeadListOut only surfaces assigned_to (not answers/sticky_notes/tasks
    # like the single-lead LeadOut does), and joinedload folds that one
    # relationship into the same query instead of lazy-loading it per row —
    # otherwise a page of N leads would cost N extra round-trips just for a
    # field the table actually shows.
    q = db.query(Lead).options(joinedload(Lead.assigned_to)).filter(Lead.deleted.is_(False))
    if status:
        q = q.filter(Lead.status == status)
    if type:
        q = q.filter(Lead.type == type)
    if user.role == UserRole.consultant:
        q = q.filter(Lead.assigned_to_id == user.id)
    elif unassigned:
        q = q.filter(Lead.assigned_to_id.is_(None))
    elif assigned_to_id is not None:
        q = q.filter(Lead.assigned_to_id == assigned_to_id)
    if search:
        like = f"%{search}%"
        q = q.filter(Lead.name.ilike(like) | Lead.email.ilike(like) | Lead.phone.ilike(like))
    # The table paginates server-side (see the CRM's leads page) — the
    # frontend needs the total match count (before skip/limit) to know how
    # many pages there are, not just the page it got back.
    response.headers["X-Total-Count"] = str(q.count())
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


@router.get("/tasks", response_model=list[LeadTaskWithLeadOut])
def list_all_tasks(
    completed: bool | None = Query(None),
    assigned_to_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(view_leads),
):
    """Every task across every lead this caller can see — backs the
    "Tâches" sidebar section. A consultant only ever sees tasks on leads
    assigned to them (same rule list_leads already applies); anyone else
    sees every task, optionally narrowed to one consultant's via
    `assigned_to_id` — that's how an admin sees "all the consultants'
    tasks" instead of just their own."""
    q = (
        db.query(LeadTask)
        .join(Lead, Lead.id == LeadTask.lead_id)
        .options(joinedload(LeadTask.lead).joinedload(Lead.assigned_to))
        .filter(Lead.deleted.is_(False))
    )
    if user.role == UserRole.consultant:
        q = q.filter(Lead.assigned_to_id == user.id)
    elif assigned_to_id is not None:
        q = q.filter(Lead.assigned_to_id == assigned_to_id)
    if completed is not None:
        q = q.filter(LeadTask.completed == completed)
    tasks = q.order_by(LeadTask.completed.asc(), LeadTask.due_date.asc()).all()
    return [
        LeadTaskWithLeadOut(
            id=t.id, comment=t.comment, action=t.action, due_date=t.due_date,
            completed=t.completed, created_at=t.created_at,
            lead=TaskLeadRef(id=t.lead.id, name=t.lead.name),
            assigned_to=t.lead.assigned_to,
        )
        for t in tasks
    ]


@router.get("/stats", response_model=LeadStatsOut)
def get_lead_stats(db: Session = Depends(get_db), user=Depends(view_leads)):
    """Aggregate KPIs for the dashboard — everything computed as grouped
    SQL queries, not by fetching every lead and counting client-side."""
    base = db.query(Lead).filter(Lead.deleted.is_(False))
    if user.role == UserRole.consultant:
        base = base.filter(Lead.assigned_to_id == user.id)

    total_leads = base.count()
    status_counts = dict(base.with_entities(Lead.status, func.count(Lead.id)).group_by(Lead.status).all())
    by_status = {s.value: status_counts.get(s, 0) for s in LeadStatus}
    converted = by_status.get(LeadStatus.converted.value, 0)
    conversion_rate = round(100 * converted / total_leads, 1) if total_leads else 0.0

    total_pipeline_value = base.filter(
        Lead.status.in_([LeadStatus.new, LeadStatus.contacted, LeadStatus.qualified]),
    ).with_entities(func.coalesce(func.sum(Lead.deal_value), 0.0)).scalar() or 0.0
    converted_value = base.filter(Lead.status == LeadStatus.converted).with_entities(
        func.coalesce(func.sum(Lead.deal_value), 0.0),
    ).scalar() or 0.0

    unread_contacts = db.query(Contact).filter(Contact.read.is_(False)).count()

    by_consultant: list[ConsultantStatOut] = []
    if user.role != UserRole.consultant:
        rows = (
            base.filter(Lead.assigned_to_id.isnot(None))
            .with_entities(
                Lead.assigned_to_id, User.name,
                func.count(Lead.id),
                func.sum(case((Lead.status == LeadStatus.converted, 1), else_=0)),
                func.coalesce(func.sum(Lead.deal_value), 0.0),
                func.coalesce(func.sum(case((Lead.status == LeadStatus.converted, Lead.deal_value), else_=0.0)), 0.0),
            )
            .join(User, User.id == Lead.assigned_to_id)
            .group_by(Lead.assigned_to_id, User.name)
            .all()
        )
        for consultant_id, name, count, converted_count, total_value, conv_value in rows:
            converted_count = converted_count or 0
            by_consultant.append(ConsultantStatOut(
                consultant_id=consultant_id, name=name, total_leads=count, converted_leads=converted_count,
                conversion_rate=round(100 * converted_count / count, 1) if count else 0.0,
                total_value=float(total_value), converted_value=float(conv_value),
            ))

    return LeadStatsOut(
        total_leads=total_leads, by_status=by_status, conversion_rate=conversion_rate,
        total_pipeline_value=float(total_pipeline_value), converted_value=float(converted_value),
        unread_contacts=unread_contacts, by_consultant=by_consultant,
    )


@router.get("/{lead_id}/activity", response_model=list[LeadActivityOut])
def list_lead_activity(lead_id: int, db: Session = Depends(get_db), user=Depends(view_leads)):
    # Hardcoded, not part of the configurable "leads" permission matrix —
    # the activity timeline shows who did what (reassignments, other
    # people's field edits), which is a level of visibility into other
    # staff's actions a consultant isn't meant to have even though they
    # can view/edit their own assigned leads.
    if user.role == UserRole.consultant:
        raise HTTPException(status_code=403, detail="Réservé aux administrateurs.")
    lead = _lead_or_404(lead_id, db, user)
    entries = (
        db.query(LeadActivity).filter(LeadActivity.lead_id == lead.id)
        .order_by(LeadActivity.created_at.desc()).all()
    )
    return [
        LeadActivityOut(
            id=e.id, actor_name=e.actor.name if e.actor else None, action=e.action,
            field=e.field, old_value=e.old_value, new_value=e.new_value,
            description=e.description, created_at=e.created_at,
        )
        for e in entries
    ]


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
            previous = lead.assigned_to.name if lead.assigned_to else None
            lead.assigned_to_id = None
            if previous:
                _log_activity(db, lead.id, user, "unassigned", description=f"Désassigné de {previous}.")
        else:
            assignee = db.get(User, payload.assigned_to_id)
            if not assignee or not assignee.active:
                raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
            previous = lead.assigned_to.name if lead.assigned_to else None
            lead.assigned_to_id = assignee.id
            _notify_assigned(db, lead, assignee, actor=user)
            _log_activity(
                db, lead.id, user, "reassigned", field="assigned_to",
                old_value=previous, new_value=assignee.name,
            )

    # One activity entry per field that actually changed, logged before the
    # values are overwritten — lets the timeline show real before/after
    # pairs instead of just "something changed".
    for field, value in updates.items():
        old_value = getattr(lead, field)
        if old_value != value:
            _log_activity(db, lead.id, user, "field_changed", field=field, old_value=old_value, new_value=value)
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
    _log_activity(db, lead.id, user, "deleted")
    db.commit()


# ── Sticky notes ──────────────────────────────────────────────────────────────

@router.post("/{lead_id}/notes", response_model=LeadNoteOut, status_code=201)
def create_note(lead_id: int, payload: LeadNoteCreate, db: Session = Depends(get_db), user=Depends(edit_leads)):
    lead = _lead_or_404(lead_id, db, user)
    note = LeadNote(lead_id=lead.id, **payload.model_dump())
    db.add(note)
    _log_activity(db, lead.id, user, "note_added")
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
    _log_activity(db, note.lead_id, user, "note_deleted")
    db.delete(note)
    db.commit()


# ── Tasks ─────────────────────────────────────────────────────────────────────

@router.post("/{lead_id}/tasks", response_model=LeadTaskOut, status_code=201)
def create_task(lead_id: int, payload: LeadTaskCreate, db: Session = Depends(get_db), user=Depends(edit_leads)):
    lead = _lead_or_404(lead_id, db, user)
    task = LeadTask(lead_id=lead.id, **payload.model_dump())
    db.add(task)
    _log_activity(db, lead.id, user, "task_added", description=task.action)
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
    updates = payload.model_dump(exclude_none=True)
    if updates.get("completed") is True:
        _log_activity(db, task.lead_id, user, "task_completed", description=task.action)
    for field, value in updates.items():
        setattr(task, field, value)
    db.commit()
    db.refresh(task)
    return task


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db), user=Depends(edit_leads)):
    task = db.get(LeadTask, task_id)
    if not task or not _visible(task.lead, user):
        raise HTTPException(status_code=404, detail="Tâche introuvable.")
    _log_activity(db, task.lead_id, user, "task_deleted", description=task.action)
    db.delete(task)
    db.commit()
