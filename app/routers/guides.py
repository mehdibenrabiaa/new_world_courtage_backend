import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session
from app.auth import get_current_user
from app.database import get_db
from app.models import Guide, GuideStatus, User
from app.schemas import GuideCreate, GuideOut, GuideUpdate
from app.config import settings

UPLOAD_DIR = Path("uploads/guides")

router = APIRouter(prefix="/guides", tags=["guides"])


@router.get("/", response_model=list[GuideOut])
def list_guides(
    category: str | None = Query(None),
    status: GuideStatus | None = Query(None),
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    q = db.query(Guide)
    if category:
        q = q.filter(Guide.category == category)
    if status:
        q = q.filter(Guide.status == status)
    return q.order_by(Guide.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/slug/{slug}", response_model=GuideOut)
def get_guide_by_slug(slug: str, db: Session = Depends(get_db)):
    guide = db.query(Guide).filter(Guide.slug == slug).first()
    if not guide:
        raise HTTPException(status_code=404, detail="Guide introuvable.")
    return guide


@router.get("/{guide_id}", response_model=GuideOut)
def get_guide(guide_id: int, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    guide = db.get(Guide, guide_id)
    if not guide:
        raise HTTPException(status_code=404, detail="Guide introuvable.")
    return guide


@router.post("/", response_model=GuideOut, status_code=201)
def create_guide(payload: GuideCreate, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    existing = db.query(Guide).filter(Guide.slug == payload.slug).first()
    if existing:
        raise HTTPException(status_code=409, detail="Un guide avec ce slug existe déjà.")
    guide = Guide(**payload.model_dump())
    db.add(guide)
    db.commit()
    db.refresh(guide)
    return guide


@router.patch("/{guide_id}", response_model=GuideOut)
def update_guide(guide_id: int, payload: GuideUpdate, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    guide = db.get(Guide, guide_id)
    if not guide:
        raise HTTPException(status_code=404, detail="Guide introuvable.")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(guide, field, value)
    db.commit()
    db.refresh(guide)
    return guide


@router.post("/{guide_id}/image", response_model=GuideOut)
async def upload_guide_image(
    guide_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    guide = db.get(Guide, guide_id)
    if not guide:
        raise HTTPException(status_code=404, detail="Guide introuvable.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # Delete old image file if one exists
    if guide.image_url:
        old_relative = guide.image_url.replace(settings.base_url, "").lstrip("/")
        old_file = Path(old_relative)
        if old_file.exists():
            old_file.unlink(missing_ok=True)

    ext = Path(file.filename or "image.jpg").suffix.lower() or ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / filename

    contents = await file.read()
    dest.write_bytes(contents)

    guide.image_url = f"{settings.base_url}/uploads/guides/{filename}"
    db.commit()
    db.refresh(guide)
    return guide


@router.delete("/{guide_id}", status_code=204)
def delete_guide(guide_id: int, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    guide = db.get(Guide, guide_id)
    if not guide:
        raise HTTPException(status_code=404, detail="Guide introuvable.")
    db.delete(guide)
    db.commit()
