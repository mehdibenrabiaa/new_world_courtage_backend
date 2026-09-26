import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import Depends, Header, HTTPException
from passlib.context import CryptContext
from sqlalchemy.orm import Session
import jwt
from app.config import settings
from app.database import get_db
from app.models import RefreshToken, RolePermission, User, UserRole

# pbkdf2_sha256 is pure Python (no bcrypt C-extension to build), which keeps
# `pip install` painless on every platform this runs on, including Windows
# dev machines without a C toolchain.
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

ALGORITHM = "HS256"
# Short-lived on purpose — this is the token that can't be revoked before it
# expires (it's a self-contained JWT, not looked up per-request). The
# refresh token below is what's actually long-lived, and it CAN be revoked,
# since it's a DB row.
ACCESS_TOKEN_TTL = timedelta(minutes=30)
REFRESH_TOKEN_TTL = timedelta(days=30)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + ACCESS_TOKEN_TTL,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def _hash_token(raw_token: str) -> str:
    # A refresh token is a high-entropy random string, not a low-entropy
    # password — a fast hash is fine (no brute-force risk to defend
    # against, unlike password_hash's pbkdf2), and a DB row lookup by hash
    # needs to be cheap on every /auth/refresh call.
    return hashlib.sha256(raw_token.encode()).hexdigest()


def issue_refresh_token(db: Session, user: User, user_agent: str | None = None) -> str:
    raw_token = secrets.token_urlsafe(48)
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + REFRESH_TOKEN_TTL,
        user_agent=user_agent,
    ))
    db.commit()
    return raw_token


def _as_aware(dt: datetime) -> datetime:
    # SQLite has no native timezone-aware column type — a DateTime(timezone=
    # True) value round-trips back naive even though it was stored as UTC
    # (Postgres doesn't have this problem, it keeps the offset). Without
    # this, comparing it to datetime.now(timezone.utc) below raises on
    # SQLite (naive vs. aware) instead of just being wrong on it.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def rotate_refresh_token(db: Session, raw_token: str, user_agent: str | None = None) -> tuple[User, str]:
    """Validates a refresh token, revokes it, and issues a replacement —
    rotation on every use so a stolen-and-reused token is detectable: if a
    caller ever presents a token that's already revoked, every other active
    token for that user is revoked too (rotation-reuse is the standard
    signal that a refresh token leaked, per OAuth2 best practice)."""
    token_hash = _hash_token(raw_token)
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if not row:
        raise HTTPException(status_code=401, detail="Session invalide, merci de vous reconnecter.")
    if row.revoked_at is not None:
        revoke_all_refresh_tokens(db, row.user_id)
        raise HTTPException(status_code=401, detail="Session invalide, merci de vous reconnecter.")
    if _as_aware(row.expires_at) < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expirée, merci de vous reconnecter.")

    user = db.get(User, row.user_id)
    if not user or not user.active:
        raise HTTPException(status_code=401, detail="Compte introuvable ou désactivé.")

    row.revoked_at = datetime.now(timezone.utc)
    new_raw_token = secrets.token_urlsafe(48)
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=_hash_token(new_raw_token),
        expires_at=datetime.now(timezone.utc) + REFRESH_TOKEN_TTL,
        user_agent=user_agent,
    ))
    db.commit()
    return user, new_raw_token


def revoke_refresh_token(db: Session, raw_token: str) -> None:
    """Used by /auth/logout — idempotent, no error either way, since
    logging out with an already-invalid token should still just succeed."""
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == _hash_token(raw_token)).first()
    if row and row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()


def revoke_all_refresh_tokens(db: Session, user_id: int) -> None:
    """"Log out everywhere" — every active refresh token for this user is
    killed at once, AND every access token already issued to them stops
    working immediately too (see sessions_revoked_at on User / the "iat"
    check in get_current_user below) — otherwise "force logout" would only
    be true once whatever access token they're holding naturally expires,
    up to ACCESS_TOKEN_TTL later. Used by reuse-detection above, a
    superadmin's "force logout" action, and password changes/deactivation
    (see routers/users.py's update_user)."""
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None),
    ).update({"revoked_at": datetime.now(timezone.utc)})
    db.query(User).filter(User.id == user_id).update({"sessions_revoked_at": datetime.now(timezone.utc)})
    db.commit()


def _decode_token(token: str) -> tuple[int, datetime]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Session invalide, merci de vous reconnecter.")
    try:
        user_id = int(payload["sub"])
        issued_at = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
    except (KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Session invalide, merci de vous reconnecter.")
    return user_id, issued_at


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
    user_id, issued_at = _decode_token(token)
    user = db.get(User, user_id)
    if not user or not user.active:
        raise HTTPException(status_code=401, detail="Compte introuvable ou désactivé.")
    if user.sessions_revoked_at and issued_at < _as_aware(user.sessions_revoked_at):
        raise HTTPException(status_code=401, detail="Session invalide, merci de vous reconnecter.")
    return user


def require_superadmin(user: User = Depends(get_current_user)) -> User:
    """User management and the permissions matrix itself are hardcoded to
    superadmin-only — deliberately NOT one of the configurable
    role_permissions rows, since letting that be delegable would let a
    lower role grant itself more access."""
    if user.role != UserRole.superadmin:
        raise HTTPException(status_code=403, detail="Réservé aux super-administrateurs.")
    return user


def require_permission(resource: str, action: str):
    """Dependency factory for the CRM's regular resources (leads, guides,
    etc.) — a superadmin always passes; every other role is checked against
    its row in role_permissions, defaulting to denied if that row somehow
    doesn't exist (see main.py's seeding, which creates the full matrix on
    first boot)."""
    def _dependency(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        if user.role == UserRole.superadmin:
            return user
        row = db.query(RolePermission).filter(
            RolePermission.role == user.role,
            RolePermission.resource == resource,
            RolePermission.action == action,
        ).first()
        if not row or not row.allowed:
            raise HTTPException(status_code=403, detail="Vous n'avez pas la permission d'effectuer cette action.")
        return user
    return _dependency
