from datetime import datetime, timedelta, timezone
from fastapi import Depends, Header, HTTPException
from passlib.context import CryptContext
from sqlalchemy.orm import Session
import jwt
from app.config import settings
from app.database import get_db
from app.models import User

# pbkdf2_sha256 is pure Python (no bcrypt C-extension to build), which keeps
# `pip install` painless on every platform this runs on, including Windows
# dev machines without a C toolchain.
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

ALGORITHM = "HS256"
TOKEN_TTL = timedelta(days=7)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "exp": datetime.now(timezone.utc) + TOKEN_TTL,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def _decode_token(token: str) -> int:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Session invalide, merci de vous reconnecter.")
    try:
        return int(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Session invalide, merci de vous reconnecter.")


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Every CRM-only route depends on this. The public site never sends an
    Authorization header, so its own routes (lead/contact submission,
    published questionnaire questions, consultant booking) must NOT use
    this dependency."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentification requise.")
    token = authorization.removeprefix("Bearer ").strip()
    user_id = _decode_token(token)
    user = db.get(User, user_id)
    if not user or not user.active:
        raise HTTPException(status_code=401, detail="Compte introuvable ou désactivé.")
    return user
