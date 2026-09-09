from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.auth import hash_password, require_superadmin
from app.database import get_db
from app.models import User, UserRole
from app.schemas import UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_superadmin)])


@router.get("/", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)):
    return db.query(User).order_by(User.name.asc()).all()


@router.post("/", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Un compte avec cet email existe déjà.")
    user = User(
        name=payload.name, email=payload.email,
        password_hash=hash_password(payload.password), role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int, payload: UserUpdate, db: Session = Depends(get_db),
    current_user: User = Depends(require_superadmin),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")

    updates = payload.model_dump(exclude_none=True, exclude={"password"})

    # A superadmin can't demote or deactivate themselves — and if they're
    # the last superadmin left, can't be demoted/deactivated by anyone else
    # either, so the account structure never ends up with zero superadmins.
    demoting = "role" in updates and updates["role"] != UserRole.superadmin
    deactivating = updates.get("active") is False
    if user.role == UserRole.superadmin and (demoting or deactivating):
        if user.id == current_user.id:
            raise HTTPException(status_code=409, detail="Vous ne pouvez pas modifier votre propre rôle de super-administrateur.")
        remaining = db.query(User).filter(
            User.role == UserRole.superadmin, User.active.is_(True), User.id != user.id,
        ).count()
        if remaining == 0:
            raise HTTPException(status_code=409, detail="Impossible : il doit toujours rester au moins un super-administrateur actif.")

    for field, value in updates.items():
        setattr(user, field, value)
    if payload.password:
        user.password_hash = hash_password(payload.password)

    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: int, db: Session = Depends(get_db),
    current_user: User = Depends(require_superadmin),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    if user.id == current_user.id:
        raise HTTPException(status_code=409, detail="Vous ne pouvez pas supprimer votre propre compte.")

    # Same "never end up with zero superadmins" guard as update_user — only
    # relevant if this account is actually an active superadmin right now.
    if user.role == UserRole.superadmin and user.active:
        remaining = db.query(User).filter(
            User.role == UserRole.superadmin, User.active.is_(True), User.id != user.id,
        ).count()
        if remaining == 0:
            raise HTTPException(status_code=409, detail="Impossible : il doit toujours rester au moins un super-administrateur actif.")

    db.delete(user)
    db.commit()
