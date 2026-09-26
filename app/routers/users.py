from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.auth import hash_password, require_superadmin, revoke_all_refresh_tokens
from app.database import get_db
from app.models import ConsultantBooking, ConsultantUnavailability, User, UserRole
from app.schemas import UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_superadmin)])


@router.get("/", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)):
    return db.query(User).order_by(User.name.asc()).all()


@router.post("/", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=409, detail="Ce nom d'utilisateur est déjà pris.")
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="Un compte avec cet email existe déjà.")
    user = User(
        name=payload.name, username=payload.username, email=payload.email,
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

    if "username" in updates and updates["username"] != user.username:
        if db.query(User).filter(User.username == updates["username"], User.id != user.id).first():
            raise HTTPException(status_code=409, detail="Ce nom d'utilisateur est déjà pris.")

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

    # A changed password or a deactivation should kill any session already
    # in progress, not just block new logins — otherwise a stolen refresh
    # token keeps working right through the "fix". Done after commit so it
    # never rolls back the actual account change if this part somehow fails.
    if payload.password or deactivating:
        revoke_all_refresh_tokens(db, user.id)

    return user


@router.post("/{user_id}/revoke-sessions", status_code=204)
def revoke_sessions(
    user_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_superadmin),
):
    """"Force logout" — revokes every active refresh token for this user
    without touching their password or account status. For the case where
    you want them signed out right now (lost device, offboarding in
    progress) but aren't otherwise changing anything about the account."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    revoke_all_refresh_tokens(db, user.id)


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

    # consultant_bookings/consultant_unavailabilities have no ON DELETE
    # behavior on their consultant_id FK — deleting a user who still has
    # either would silently orphan those rows (exactly what happened
    # earlier: reallocated-away test consultants left behind bookings that
    # rendered as "Consultant supprimé" until someone noticed and cleaned
    # the table up by hand). Blocking here instead of cascading, since a
    # cascade would destroy real appointment history — reallocate their
    # bookings and clear their calendar blocks first.
    booking_count = db.query(ConsultantBooking).filter(ConsultantBooking.consultant_id == user.id).count()
    if booking_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Impossible : ce compte a encore {booking_count} rendez-vous. Réaffectez-les avant de supprimer le compte.",
        )
    block_count = db.query(ConsultantUnavailability).filter(ConsultantUnavailability.consultant_id == user.id).count()
    if block_count > 0:
        raise HTTPException(
            status_code=409,
            detail="Impossible : ce compte a encore des blocages de calendrier. Videz son calendrier avant de supprimer le compte.",
        )

    db.delete(user)
    db.commit()
