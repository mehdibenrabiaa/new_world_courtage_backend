import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
import jwt
from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session
from app import oauth
from app.account_auth import (
    consume_password_reset_token, create_account_access_token, create_password_reset_token,
    generate_referral_code, get_current_account, hash_password, issue_account_refresh_token,
    revoke_account_refresh_token, rotate_account_refresh_token, verify_password,
)
from app.config import settings
from app.database import get_db
from app.email import (
    send_account_email_changed_notice, send_account_password_changed_notice,
    send_account_password_reset_email, send_account_welcome_email,
)
from app.models import Account, AccountType, Lead, LeadDocument, OAuthProvider
from app.schemas import (
    AccountChangePasswordRequest, AccountForgotPasswordRequest, AccountLeadOut, AccountLoginRequest,
    AccountOut, AccountRefreshedTokenOut, AccountRefreshRequest, AccountRegisterRequest,
    AccountReferralOut, AccountResetPasswordRequest, AccountTokenOut, AccountUpdateRequest, LeadDocumentOut,
)

# Same on-disk layout leads.py's document upload/download routes use
# (uploads/leads/<lead_id>/<stored_filename>) — duplicated here rather than
# imported from routers.leads to avoid a cross-router import for one constant.
LEAD_UPLOAD_ROOT = Path("uploads/leads")

logger = logging.getLogger("app.accounts")
router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("/register", response_model=AccountTokenOut)
def register(payload: AccountRegisterRequest, user_agent: str | None = Header(default=None), db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    if db.query(Account).filter(Account.email == email).first():
        raise HTTPException(status_code=409, detail="Un compte existe déjà avec cet email.")

    account = Account(
        name=payload.name,
        email=email,
        password_hash=hash_password(payload.password),
        type=payload.type,
        referral_code=generate_referral_code(db) if payload.type == AccountType.partenaire else None,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    logger.info("Account %s (id=%s, type=%s) registered", account.email, account.id, account.type.value)
    send_account_welcome_email(account.name, account.email, account.type.value)
    return AccountTokenOut(
        access_token=create_account_access_token(account),
        refresh_token=issue_account_refresh_token(db, account, user_agent),
        account=AccountOut.model_validate(account),
    )


@router.post("/login", response_model=AccountTokenOut)
def login(payload: AccountLoginRequest, user_agent: str | None = Header(default=None), db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    account = db.query(Account).filter(Account.email == email).first()
    if not account or not account.active or not account.password_hash or not verify_password(payload.password, account.password_hash):
        # Covers "no such account", "OAuth-only account with no password",
        # and "wrong password" with one identical message — never reveal
        # which of the three it was.
        logger.warning("Failed account login attempt for email=%r", email)
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect.")
    return AccountTokenOut(
        access_token=create_account_access_token(account),
        refresh_token=issue_account_refresh_token(db, account, user_agent),
        account=AccountOut.model_validate(account),
    )


@router.post("/refresh", response_model=AccountRefreshedTokenOut)
def refresh(payload: AccountRefreshRequest, user_agent: str | None = Header(default=None), db: Session = Depends(get_db)):
    account, new_refresh_token = rotate_account_refresh_token(db, payload.refresh_token, user_agent)
    return AccountRefreshedTokenOut(access_token=create_account_access_token(account), refresh_token=new_refresh_token)


@router.post("/logout", status_code=204)
def logout(payload: AccountRefreshRequest, db: Session = Depends(get_db)):
    revoke_account_refresh_token(db, payload.refresh_token)


@router.get("/me", response_model=AccountOut)
def me(current_account: Account = Depends(get_current_account)):
    return AccountOut.model_validate(current_account)


@router.patch("/me", response_model=AccountOut)
def update_me(
    payload: AccountUpdateRequest,
    current_account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    old_email = current_account.email
    email_changing = False

    if payload.email is not None:
        new_email = payload.email.strip().lower()
        if new_email != current_account.email:
            # Email is what "mot de passe oublié" sends the reset link to —
            # changing it is just as sensitive as changing the password
            # itself, so it gets the same current-password re-check
            # (skipped only for an OAuth-only account, which has no
            # password to check in the first place).
            if current_account.password_hash:
                if not payload.current_password:
                    raise HTTPException(
                        status_code=400,
                        detail="Le mot de passe actuel est requis pour changer d'adresse email.",
                    )
                if not verify_password(payload.current_password, current_account.password_hash):
                    raise HTTPException(status_code=401, detail="Mot de passe actuel incorrect.")
            if db.query(Account).filter(Account.email == new_email, Account.id != current_account.id).first():
                raise HTTPException(status_code=409, detail="Un compte existe déjà avec cet email.")
            current_account.email = new_email
            email_changing = True
    if payload.name is not None:
        current_account.name = payload.name
    db.commit()
    db.refresh(current_account)

    if email_changing:
        # Best-effort, to the OLD address — see send_account_email_changed_notice.
        send_account_email_changed_notice(current_account.name, old_email, current_account.email)

    return AccountOut.model_validate(current_account)


@router.post("/me/change-password", status_code=204)
def change_password(
    payload: AccountChangePasswordRequest,
    current_account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    if not current_account.password_hash:
        raise HTTPException(
            status_code=400,
            detail="Ce compte utilise la connexion via un fournisseur externe, il n'y a pas de mot de passe à changer.",
        )
    if not verify_password(payload.current_password, current_account.password_hash):
        raise HTTPException(status_code=401, detail="Mot de passe actuel incorrect.")
    current_account.password_hash = hash_password(payload.new_password)
    db.commit()
    send_account_password_changed_notice(current_account.name, current_account.email)


@router.get("/me/leads", response_model=list[AccountLeadOut])
def my_leads(current_account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    """Espace Client — matched by email since a lead is submitted before any
    account exists (there's no lead_id/account_id link at submission time).
    A client with no matching leads just sees an empty list, not an error."""
    if current_account.type != AccountType.client:
        raise HTTPException(status_code=403, detail="Réservé aux comptes client.")
    leads = (
        db.query(Lead)
        .filter(Lead.email == current_account.email, Lead.deleted.is_(False))
        .order_by(Lead.created_at.desc())
        .all()
    )
    return leads


@router.get("/me/referral", response_model=AccountReferralOut)
def my_referral(current_account: Account = Depends(get_current_account)):
    """Espace Partenaire — the referral link just carries ?ref=<code> back
    to the homepage for now; tracking which leads actually came from it is
    a later step, not part of this MVP."""
    if current_account.type != AccountType.partenaire:
        raise HTTPException(status_code=403, detail="Réservé aux comptes partenaire.")
    if not current_account.referral_code:
        raise HTTPException(status_code=500, detail="Aucun code de parrainage associé à ce compte.")
    return AccountReferralOut(
        code=current_account.referral_code,
        link=f"{settings.frontend_url}/?ref={current_account.referral_code}",
    )


def _my_lead_ids(current_account: Account, db: Session) -> list[int]:
    return [
        row[0] for row in db.query(Lead.id).filter(
            Lead.email == current_account.email, Lead.deleted.is_(False),
        ).all()
    ]


@router.get("/me/documents", response_model=list[LeadDocumentOut])
def my_documents(current_account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    """Espace Client — every document attached to any of the account's own
    leads (matched by email, same as /me/leads), newest first."""
    if current_account.type != AccountType.client:
        raise HTTPException(status_code=403, detail="Réservé aux comptes client.")
    lead_ids = _my_lead_ids(current_account, db)
    if not lead_ids:
        return []
    return (
        db.query(LeadDocument)
        .filter(LeadDocument.lead_id.in_(lead_ids))
        .order_by(LeadDocument.created_at.desc())
        .all()
    )


@router.get("/me/documents/{document_id}/download")
def download_my_document(
    document_id: int,
    current_account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    if current_account.type != AccountType.client:
        raise HTTPException(status_code=403, detail="Réservé aux comptes client.")
    document = db.get(LeadDocument, document_id)
    # Ownership check happens against the account's own lead ids, never
    # against the document's lead_id directly — otherwise any authenticated
    # client could download any other client's documents just by guessing ids.
    if not document or document.lead_id not in _my_lead_ids(current_account, db):
        raise HTTPException(status_code=404, detail="Document introuvable.")
    path = LEAD_UPLOAD_ROOT / str(document.lead_id) / document.stored_filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    return FileResponse(path, filename=document.original_filename, media_type=document.content_type or "application/octet-stream")


@router.post("/forgot-password", status_code=204)
def forgot_password(payload: AccountForgotPasswordRequest, db: Session = Depends(get_db)):
    """Always 204, whether or not the email matches an account — an
    "email not found" response here would let anyone enumerate registered
    emails one guess at a time."""
    email = payload.email.strip().lower()
    account = db.query(Account).filter(Account.email == email, Account.active.is_(True)).first()
    if account:
        raw_token = create_password_reset_token(db, account)
        reset_url = f"{settings.frontend_url}/reinitialiser-mot-de-passe?token={raw_token}"
        send_account_password_reset_email(account.name, account.email, reset_url)


@router.post("/reset-password", status_code=204)
def reset_password(payload: AccountResetPasswordRequest, db: Session = Depends(get_db)):
    account = consume_password_reset_token(db, payload.token)
    account.password_hash = hash_password(payload.password)
    db.commit()


# ── Social sign-in ──────────────────────────────────────────────────────────
# There is no server-side session to stash a CSRF nonce in (every other
# endpoint here is stateless JWT auth, on purpose), so `state` is itself a
# short-lived signed JWT carrying {nonce, type} — tamper-proof and
# self-expiring without needing a session store. `type` only matters the
# first time (a brand-new account); an existing OAuth identity or a matching
# email logs in as whatever type that account already is.
_STATE_ALGORITHM = "HS256"


def _encode_state(account_type: str) -> str:
    payload = {
        "type": account_type,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=_STATE_ALGORITHM)


def _decode_state(state: str) -> str:
    try:
        payload = jwt.decode(state, settings.secret_key, algorithms=[_STATE_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=400, detail="Requête de connexion invalide ou expirée.")
    return payload["type"]


def _callback_redirect_uri(provider: str) -> str:
    return f"{settings.base_url}/api/accounts/oauth/{provider}/callback"


def _upsert_oauth_account(db: Session, provider: OAuthProvider, profile: "oauth.OAuthProfile", account_type: str) -> Account:
    account = db.query(Account).filter(
        Account.oauth_provider == provider, Account.oauth_subject == profile.subject,
    ).first()
    if account:
        return account

    # Fall back to matching an existing password/other-provider account by
    # email, so someone who already signed up with a password (or a
    # different provider) and then hits "Sign in with Google" lands on the
    # same account instead of silently getting a second one.
    if profile.email:
        account = db.query(Account).filter(Account.email == profile.email.lower()).first()
        if account:
            account.oauth_provider = provider
            account.oauth_subject = profile.subject
            db.commit()
            db.refresh(account)
            return account

    account_type_enum = AccountType(account_type)
    account = Account(
        name=profile.name or (profile.email.split("@")[0] if profile.email else "Compte"),
        email=(profile.email or f"{provider.value}-{profile.subject}@oauth.newworldcourtage.invalid").lower(),
        password_hash=None,
        type=account_type_enum,
        referral_code=generate_referral_code(db) if account_type_enum == AccountType.partenaire else None,
        oauth_provider=provider,
        oauth_subject=profile.subject,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    send_account_welcome_email(account.name, account.email, account.type.value)
    return account


def _finish_oauth_login(db: Session, provider: OAuthProvider, profile: "oauth.OAuthProfile", account_type: str) -> RedirectResponse:
    account = _upsert_oauth_account(db, provider, profile, account_type)
    if not account.active:
        raise HTTPException(status_code=403, detail="Compte désactivé.")
    access_token = create_account_access_token(account)
    refresh_token = issue_account_refresh_token(db, account)
    # The frontend's /oauth-callback page reads these from the URL, stores
    # them, then redirects into the right espace — see pages/oauth-callback.
    target = f"{settings.frontend_url}/oauth-callback?access_token={access_token}&refresh_token={refresh_token}"
    return RedirectResponse(target)


@router.get("/oauth/{provider}/start")
def oauth_start(provider: str, type: str = "client"):
    if provider not in oauth.PROVIDERS:
        raise HTTPException(status_code=404, detail="Fournisseur de connexion inconnu.")
    if type not in (AccountType.client.value, AccountType.partenaire.value):
        raise HTTPException(status_code=400, detail="Type de compte invalide.")
    state = _encode_state(type)
    url = oauth.authorize_url(provider, state, _callback_redirect_uri(provider))
    return RedirectResponse(url)


@router.get("/oauth/google/callback")
def oauth_google_callback(code: str, state: str, db: Session = Depends(get_db)):
    account_type = _decode_state(state)
    profile = oauth.exchange_code("google", code, _callback_redirect_uri("google"))
    return _finish_oauth_login(db, OAuthProvider.google, profile, account_type)


@router.get("/oauth/facebook/callback")
def oauth_facebook_callback(code: str, state: str, db: Session = Depends(get_db)):
    account_type = _decode_state(state)
    profile = oauth.exchange_code("facebook", code, _callback_redirect_uri("facebook"))
    return _finish_oauth_login(db, OAuthProvider.facebook, profile, account_type)


@router.post("/oauth/apple/callback")
def oauth_apple_callback(code: str = Form(...), state: str = Form(...), db: Session = Depends(get_db)):
    # Apple posts here as a form submission (response_mode=form_post), not a
    # query-string GET like Google/Facebook — see oauth.authorize_url.
    account_type = _decode_state(state)
    profile = oauth.exchange_code("apple", code, _callback_redirect_uri("apple"))
    return _finish_oauth_login(db, OAuthProvider.apple, profile, account_type)
