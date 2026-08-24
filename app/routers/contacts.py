from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Contact
from app.schemas import ContactCreate, ContactOut

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.post("/", response_model=ContactOut, status_code=201)
def create_contact(payload: ContactCreate, db: Session = Depends(get_db)):
    contact = Contact(**payload.model_dump())
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


@router.get("/", response_model=list[ContactOut])
def list_contacts(
    unread_only: bool = Query(False),
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    q = db.query(Contact)
    if unread_only:
        q = q.filter(Contact.read == False)
    return q.order_by(Contact.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/{contact_id}", response_model=ContactOut)
def get_contact(contact_id: int, db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact introuvable.")
    return contact


@router.patch("/{contact_id}/read", response_model=ContactOut)
def mark_read(contact_id: int, db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact introuvable.")
    contact.read = True
    db.commit()
    db.refresh(contact)
    return contact


@router.delete("/{contact_id}", status_code=204)
def delete_contact(contact_id: int, db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact introuvable.")
    db.delete(contact)
    db.commit()
