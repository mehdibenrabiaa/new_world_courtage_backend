import hashlib
import secrets
import string
from datetime import datetime, timedelta, timezone
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session
import jwt
from app.auth import hash_password, verify_password  # noqa: F401 — re-exported for routers/accounts.py
from app.config import settings
from app.database import get_db
from app.models import Account, AccountPasswordResetToken, AccountRefreshToken, AccountType

ALGORITHM = "HS256"
# Short-lived on purpose: a token left in a browser after logout dies quickly;
# active users don't notice since the frontend renews it with the refresh token.
ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(days=30)
RESET_TOKEN_TTL = timedelta(hours=1)

# A distinct "scope" claim on every account access token — without this, a
# customer's JWT and a CRM staff JWT are structurally identical (same "sub"/
# "iat"/"exp" shape) and get_current_user/get_current_account would each
# happily decode the other's token and look up an unrelated row by id.
ACCOUNT_TOKEN_SCOPE = "account"


def create_account_access_token(account: Account) -> str:
    payload = {
        "sub": str(account.id),
        "scope": ACCOUNT_TOKEN_SCOPE,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + ACCESS_TOKEN_TTL,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def issue_account_refresh_token(db: Session, account: Account, user_agent: str | None = None) -> str:
    raw_token = secrets.token_urlsafe(48)
    db.add(AccountRefreshToken(
        account_id=account.id,
        token_hash=_hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + REFRESH_TOKEN_TTL,
        user_agent=user_agent,
    ))
    db.commit()
    return raw_token


def _as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def rotate_account_refresh_token(db: Session, raw_token: str, user_agent: str | None = None) -> tuple[Account, str]:
    """Same rotation-on-use / reuse-detection scheme as app.auth's
    rotate_refresh_token — see that function's docstring."""
    token_hash = _hash_token(raw_token)
    row = db.query(AccountRefreshToken).filter(AccountRefreshToken.token_hash == token_hash).first()
    if not row:
        raise HTTPException(status_code=401, detail="Session invalide, merci de vous reconnecter.")
    if row.revoked_at is not None:
        revoke_all_account_refresh_tokens(db, row.account_id)
        raise HTTPException(status_code=401, detail="Session invalide, merci de vous reconnecter.")
    if _as_aware(row.expires_at) < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expirée, merci de vous reconnecter.")

    account = db.get(Account, row.account_id)
    if not account or not account.active:
        raise HTTPException(status_code=401, detail="Compte introuvable ou désactivé.")

    row.revoked_at = datetime.now(timezone.utc)
    new_raw_token = secrets.token_urlsafe(48)
    db.add(AccountRefreshToken(
        account_id=account.id,
        token_hash=_hash_token(new_raw_token),
        expires_at=datetime.now(timezone.utc) + REFRESH_TOKEN_TTL,
        user_agent=user_agent,
    ))
    db.commit()
    return account, new_raw_token


def revoke_account_refresh_token(db: Session, raw_token: str) -> None:
    row = db.query(AccountRefreshToken).filter(AccountRefreshToken.token_hash == _hash_token(raw_token)).first()
    if row and row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()


def revoke_all_account_refresh_tokens(db: Session, account_id: int) -> None:
    db.query(AccountRefreshToken).filter(
        AccountRefreshToken.account_id == account_id, AccountRefreshToken.revoked_at.is_(None),
    ).update({"revoked_at": datetime.now(timezone.utc)})
    db.query(Account).filter(Account.id == account_id).update({"sessions_revoked_at": datetime.now(timezone.utc)})
    db.commit()


def get_current_account(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Account:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentification requise.")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Session invalide, merci de vous reconnecter.")
    if payload.get("scope") != ACCOUNT_TOKEN_SCOPE:
        raise HTTPException(status_code=401, detail="Session invalide, merci de vous reconnecter.")
    try:
        account_id = int(payload["sub"])
        issued_at = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
    except (KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Session invalide, merci de vous reconnecter.")

    account = db.get(Account, account_id)
    if not account or not account.active:
        raise HTTPException(status_code=401, detail="Compte introuvable ou désactivé.")
    if account.sessions_revoked_at and issued_at < _as_aware(account.sessions_revoked_at):
        raise HTTPException(status_code=401, detail="Session invalide, merci de vous reconnecter.")
    return account


_REFERRAL_ALPHABET = string.ascii_uppercase + string.digits


def generate_referral_code(db: Session) -> str:
    """8-char, human-shareable code — retried on the (astronomically rare)
    collision rather than trusting uniqueness up front, since it's cheap
    and avoids a race with a concurrent signup."""
    for _ in range(20):
        code = "".join(secrets.choice(_REFERRAL_ALPHABET) for _ in range(8))
        if not db.query(Account).filter(Account.referral_code == code).first():
            return code
    raise HTTPException(status_code=500, detail="Impossible de générer un code de parrainage, réessayez.")


def create_password_reset_token(db: Session, account: Account) -> str:
    raw_token = secrets.token_urlsafe(32)
    db.add(AccountPasswordResetToken(
        account_id=account.id,
        token_hash=_hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + RESET_TOKEN_TTL,
    ))
    db.commit()
    return raw_token


def consume_password_reset_token(db: Session, raw_token: str) -> Account:
    """Validates and immediately marks the token used — a reset link only
    ever works once, even if the request somehow fires twice."""
    row = db.query(AccountPasswordResetToken).filter(
        AccountPasswordResetToken.token_hash == _hash_token(raw_token)
    ).first()
    if not row or row.used_at is not None or _as_aware(row.expires_at) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Ce lien de réinitialisation est invalide ou a expiré.")
    account = db.get(Account, row.account_id)
    if not account or not account.active:
        raise HTTPException(status_code=400, detail="Compte introuvable ou désactivé.")
    row.used_at = datetime.now(timezone.utc)
    db.commit()
    return account
