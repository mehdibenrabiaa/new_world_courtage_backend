from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.auth import require_superadmin
from app.database import get_db
from app.models import PermissionAction, PermissionResource, RolePermission, UserRole
from app.schemas import RolePermissionOut, RolePermissionUpdate

router = APIRouter(prefix="/permissions", tags=["permissions"], dependencies=[Depends(require_superadmin)])

# superadmin is intentionally excluded — it always has every permission,
# hardcoded in app/auth.py's require_permission, and is never represented
# as editable rows here.
CONFIGURABLE_ROLES = [UserRole.admin, UserRole.supervisor, UserRole.consultant]


@router.get("/", response_model=list[RolePermissionOut])
def list_permissions(db: Session = Depends(get_db)):
    return db.query(RolePermission).order_by(
        RolePermission.role, RolePermission.resource, RolePermission.action,
    ).all()


@router.patch("/{role}/{resource}/{action}", response_model=RolePermissionOut)
def update_permission(
    role: UserRole, resource: PermissionResource, action: PermissionAction,
    payload: RolePermissionUpdate, db: Session = Depends(get_db),
):
    if role not in CONFIGURABLE_ROLES:
        raise HTTPException(status_code=400, detail="Le rôle super-administrateur n'est pas configurable.")

    row = db.query(RolePermission).filter(
        RolePermission.role == role, RolePermission.resource == resource, RolePermission.action == action,
    ).first()
    if not row:
        row = RolePermission(role=role, resource=resource, action=action)
        db.add(row)

    row.allowed = payload.allowed
    db.commit()
    db.refresh(row)
    return row
