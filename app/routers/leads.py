from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Lead, LeadStatus
from app.schemas import LeadCreate, LeadOut, LeadUpdate

router = APIRouter(prefix="/leads", tags=["leads"])


@router.post("/", response_model=LeadOut, status_code=201)
def create_lead(payload: LeadCreate, db: Session = Depends(get_db)):
    lead = Lead(**payload.model_dump())
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


@router.get("/", response_model=list[LeadOut])
def list_leads(
    status: LeadStatus | None = Query(None),
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    q = db.query(Lead)
    if status:
        q = q.filter(Lead.status == status)
    return q.order_by(Lead.created_at.desc()).offset(skip).limit(limit).all()


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
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(lead, field, value)
    db.commit()
    db.refresh(lead)
    return lead


@router.delete("/{lead_id}", status_code=204)
def delete_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead introuvable.")
    db.delete(lead)
    db.commit()
