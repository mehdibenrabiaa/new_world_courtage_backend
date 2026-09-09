from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.auth import get_current_user
from app.database import get_db
from app.models import Guide, Author
from app.config import settings

router = APIRouter(prefix="/media", tags=["media"], dependencies=[Depends(get_current_user)])

UPLOAD_ROOT = Path("uploads")


class MediaFile(BaseModel):
    path: str
    url: str
    category: str
    size: int
    modified_at: datetime
    in_use: bool
    used_by: list[str]


def _upload_path_of(url: str | None) -> str | None:
    """Extract the path relative to uploads/ from a stored absolute URL,
    ignoring whatever host/port it was saved with — that value has drifted
    before (see BASE_URL history) and isn't reliable for matching."""
    if not url:
        return None
    marker = "/uploads/"
    idx = url.find(marker)
    if idx == -1:
        return None
    return url[idx + len(marker):]


def _usage_map(db: Session) -> dict[str, list[str]]:
    usage: dict[str, list[str]] = {}
    for guide in db.query(Guide).filter(Guide.image_url.isnot(None)).all():
        key = _upload_path_of(guide.image_url)
        if key:
            usage.setdefault(key, []).append(f"Guide : {guide.title}")
    for author in db.query(Author).filter(Author.avatar_url.isnot(None)).all():
        key = _upload_path_of(author.avatar_url)
        if key:
            usage.setdefault(key, []).append(f"Auteur : {author.name}")
    return usage


@router.get("/", response_model=list[MediaFile])
def list_media(db: Session = Depends(get_db)):
    if not UPLOAD_ROOT.exists():
        return []

    usage = _usage_map(db)
    files: list[MediaFile] = []
    for p in UPLOAD_ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(UPLOAD_ROOT).as_posix()
        stat = p.stat()
        files.append(MediaFile(
            path=rel,
            url=f"{settings.base_url}/uploads/{rel}",
            category=rel.split("/")[0] if "/" in rel else "autre",
            size=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            in_use=rel in usage,
            used_by=usage.get(rel, []),
        ))
    files.sort(key=lambda f: f.modified_at, reverse=True)
    return files


@router.delete("/{file_path:path}", status_code=204)
def delete_media(file_path: str, force: bool = Query(False), db: Session = Depends(get_db)):
    root = UPLOAD_ROOT.resolve()
    target = (UPLOAD_ROOT / file_path).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=400, detail="Chemin invalide.")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Fichier introuvable.")

    if not force:
        used_by = _usage_map(db).get(file_path, [])
        if used_by:
            raise HTTPException(
                status_code=409,
                detail=f"Ce fichier est utilisé par : {', '.join(used_by)}.",
            )

    target.unlink()
