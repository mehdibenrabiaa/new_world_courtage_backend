from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.auth import create_access_token, get_current_user, verify_password
from app.database import get_db
from app.models import PermissionAction, PermissionResource, RolePermission, User, UserRole
from app.schemas import LoginRequest, MeOut, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username.strip().lower()).first()
    if not user or not user.active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Nom d'utilisateur ou mot de passe incorrect.")
    return TokenOut(access_token=create_access_token(user), user=UserOut.model_validate(user))


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
