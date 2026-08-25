import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Author, Guide
from app.schemas import AuthorCreate, AuthorUpdate, AuthorOut
from app.config import settings

UPLOAD_DIR = Path("uploads/authors")

router = APIRouter(prefix="/authors", tags=["authors"])


def _delete_avatar_file(avatar_url: str | None) -> None:
    if not avatar_url:
        return
    relative = avatar_url.replace(settings.base_url, "").lstrip("/")
    file = Path(relative)
    if file.exists():
        file.unlink(missing_ok=True)


@router.get("/", response_model=list[AuthorOut])
def list_authors(db: Session = Depends(get_db)):
    return db.query(Author).order_by(Author.name.asc()).all()


@router.post("/", response_model=AuthorOut, status_code=201)
def create_author(payload: AuthorCreate, db: Session = Depends(get_db)):
    existing = db.query(Author).filter(Author.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Un auteur avec ce nom existe déjà.")
    author = Author(**payload.model_dump())
    db.add(author)
    db.commit()
    db.refresh(author)
    return author


@router.patch("/{author_id}", response_model=AuthorOut)
def update_author(author_id: int, payload: AuthorUpdate, db: Session = Depends(get_db)):
    author = db.get(Author, author_id)
    if not author:
        raise HTTPException(status_code=404, detail="Auteur introuvable.")

    old_name = author.name
    data = payload.model_dump(exclude_none=True)
    new_name = data.get("name")

    if new_name and new_name != old_name:
        conflict = db.query(Author).filter(Author.name == new_name, Author.id != author_id).first()
        if conflict:
            raise HTTPException(status_code=409, detail="Un auteur avec ce nom existe déjà.")

    for field, value in data.items():
        setattr(author, field, value)

    # Guides store the author/editor/reviewer name as plain text (no FK) —
    # keep them in sync so a rename doesn't silently orphan the byline.
    if new_name and new_name != old_name:
        db.query(Guide).filter(Guide.author_name == old_name).update({"author_name": new_name})
        db.query(Guide).filter(Guide.editor_name == old_name).update({"editor_name": new_name})
        db.query(Guide).filter(Guide.reviewer_name == old_name).update({"reviewer_name": new_name})

    db.commit()
    db.refresh(author)
    return author


@router.post("/{author_id}/image", response_model=AuthorOut)
async def upload_author_image(
    author_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    author = db.get(Author, author_id)
    if not author:
        raise HTTPException(status_code=404, detail="Auteur introuvable.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _delete_avatar_file(author.avatar_url)

    ext = Path(file.filename or "image.jpg").suffix.lower() or ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / filename

    contents = await file.read()
    dest.write_bytes(contents)

    author.avatar_url = f"{settings.base_url}/uploads/authors/{filename}"
    db.commit()
    db.refresh(author)
    return author


@router.delete("/{author_id}", status_code=204)
def delete_author(author_id: int, db: Session = Depends(get_db)):
    author = db.get(Author, author_id)
    if not author:
        raise HTTPException(status_code=404, detail="Auteur introuvable.")

    in_use = db.query(Guide).filter(Guide.author_name == author.name).count()
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=f"Cet auteur est utilisé par {in_use} guide(s). Changez leur auteur avant de le supprimer.",
        )

    _delete_avatar_file(author.avatar_url)
    db.delete(author)
    db.commit()
