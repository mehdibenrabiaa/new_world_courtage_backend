from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from app.auth import require_permission
from app.database import get_db
from app.models import Account, AccountType, Lead
from app.schemas import AdminAccountDetailOut, AdminAccountOut

# Read-only for now — this is the CRM's view onto the public site's
# Account table (Espace Client / Espace Partenaire), gated behind the same
# permission as the Contacts page since both are "look up someone who
# reached us through the public site" tools. Account management (deactivate,
# etc.) isn't needed yet; add edit_accounts/delete_accounts here the same
# way contacts.py does if that changes.
router = APIRouter(prefix="/crm-accounts", tags=["crm-accounts"])

view_accounts = require_permission("contacts", "view")


def _leads_count_by_email(db: Session, emails: list[str]) -> dict[str, int]:
    if not emails:
        return {}
    rows = (
        db.query(Lead.email, func.count(Lead.id))
        .filter(Lead.email.in_(emails), Lead.deleted.is_(False))
        .group_by(Lead.email)
        .all()
    )
    return dict(rows)


def _to_admin_out(account: Account, leads_count: int) -> AdminAccountOut:
    return AdminAccountOut(
        id=account.id,
        name=account.name,
        email=account.email,
        type=account.type,
        referral_code=account.referral_code,
        active=account.active,
        oauth_provider=account.oauth_provider.value if account.oauth_provider else None,
        leads_count=leads_count,
        created_at=account.created_at,
    )


@router.get("/", response_model=list[AdminAccountOut])
def list_accounts(
    response: Response,
    type: AccountType | None = Query(None),
    search: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
    _user=Depends(view_accounts),
):
    q = db.query(Account)
    if type:
        q = q.filter(Account.type == type)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Account.name.ilike(like), Account.email.ilike(like)))

    # Same reasoning as leads.py's list_leads — the CRM table paginates
    # server-side and needs the total match count (before skip/limit) to
    # know how many pages there are.
    response.headers["X-Total-Count"] = str(q.count())
    accounts = q.order_by(Account.created_at.desc()).offset(skip).limit(limit).all()

    client_emails = [a.email for a in accounts if a.type == AccountType.client]
    counts = _leads_count_by_email(db, client_emails)
    return [_to_admin_out(a, counts.get(a.email, 0)) for a in accounts]


@router.get("/{account_id}", response_model=AdminAccountDetailOut)
def get_account(account_id: int, db: Session = Depends(get_db), _user=Depends(view_accounts)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Compte introuvable.")

    leads = []
    if account.type == AccountType.client:
        leads = (
            db.query(Lead)
            .filter(Lead.email == account.email, Lead.deleted.is_(False))
            .order_by(Lead.created_at.desc())
            .all()
        )

    base = _to_admin_out(account, len(leads))
    return AdminAccountDetailOut(**base.model_dump(), leads=leads)
