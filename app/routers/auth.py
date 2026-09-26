import logging
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from app.auth import (
    create_access_token, get_current_user, issue_refresh_token, revoke_refresh_token,
    rotate_refresh_token, verify_password,
)
from app.database import get_db
from app.models import PermissionAction, PermissionResource, RolePermission, User, UserRole
from app.schemas import LoginRequest, MeOut, RefreshedTokenOut, RefreshRequest, TokenOut, UserOut

logger = logging.getLogger("app.auth")
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, user_agent: str | None = Header(default=None), db: Session = Depends(get_db)):
    username = payload.username.strip().lower()
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.active or not verify_password(payload.password, user.password_hash):
        # Username only — never the password, even on failure.
        logger.warning("Failed login attempt for username=%r", username)
        raise HTTPException(status_code=401, detail="Nom d'utilisateur ou mot de passe incorrect.")
    logger.info("User %s (id=%s) logged in", user.username, user.id)
    return TokenOut(
        access_token=create_access_token(user),
        refresh_token=issue_refresh_token(db, user, user_agent),
        user=UserOut.model_validate(user),
    )


@router.post("/refresh", response_model=RefreshedTokenOut)
def refresh(payload: RefreshRequest, user_agent: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Exchanges a still-valid refresh token for a new short-lived access
    token, rotating the refresh token in the same call — the frontend's
    authFetch calls this automatically on a 401 before giving up and
    forcing a re-login (see lib/auth.ts)."""
    user, new_refresh_token = rotate_refresh_token(db, payload.refresh_token, user_agent)
    return RefreshedTokenOut(access_token=create_access_token(user), refresh_token=new_refresh_token)


@router.post("/logout", status_code=204)
def logout(payload: RefreshRequest, db: Session = Depends(get_db)):
    """Revokes just this one session's refresh token — the access token
    already in flight stays valid until it naturally expires (at most 30
    minutes later), same trade-off every access/refresh-token setup makes."""
    revoke_refresh_token(db, payload.refresh_token)


def _resolved_permissions(user: User, db: Session) -> dict[str, list[str]]:
    resources = [r.value for r in PermissionResource]
    actions = [a.value for a in PermissionAction]

    if user.role == UserRole.superadmin:
        return {resource: list(actions) for resource in resources}

    allowed_rows = db.query(RolePermission).filter(
        RolePermission.role == user.role, RolePermission.allowed.is_(True),
    ).all()
    result: dict[str, list[str]] = {resource: [] for resource in resources}
    for row in allowed_rows:
        result[row.resource.value].append(row.action.value)
    return result


@router.get("/me", response_model=MeOut)
def me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return MeOut(
        **UserOut.model_validate(current_user).model_dump(),
        permissions=_resolved_permissions(current_user, db),
    )
