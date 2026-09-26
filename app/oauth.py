"""Sign in with Google / Apple / Facebook for public-site accounts.

Each provider needs real credentials (client id/secret, and for Apple a
Services ID + private key) registered with that provider before any of this
actually works — see the settings in app/config.py. Until they're filled in,
`authorize_url` raises a 501 the frontend can show as "pas encore disponible"
rather than sending the user into a broken redirect.
"""
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import httpx
import jwt
from fastapi import HTTPException
from app.config import settings

PROVIDERS = {"google", "apple", "facebook"}


class OAuthProfile:
    def __init__(self, subject: str, email: str | None, name: str | None):
        self.subject = subject
        self.email = email
        self.name = name


def _configured(provider: str) -> bool:
    if provider == "google":
        return bool(settings.google_client_id and settings.google_client_secret)
    if provider == "facebook":
        return bool(settings.facebook_client_id and settings.facebook_client_secret)
    if provider == "apple":
        return bool(
            settings.apple_client_id and settings.apple_team_id
            and settings.apple_key_id and settings.apple_private_key
        )
    return False


def _require_configured(provider: str) -> None:
    if not _configured(provider):
        raise HTTPException(
            status_code=501,
            detail=f"La connexion via {provider.capitalize()} n'est pas encore configurée.",
        )


def authorize_url(provider: str, state: str, redirect_uri: str) -> str:
    _require_configured(provider)

    if provider == "google":
        params = {
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    if provider == "facebook":
        params = {
            "client_id": settings.facebook_client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": "email,public_profile",
        }
        return f"https://www.facebook.com/v19.0/dialog/oauth?{urlencode(params)}"

    if provider == "apple":
        # response_mode=form_post is required by Apple whenever "name" or
        # "email" scope is requested — the callback route must accept POST,
        # not GET, unlike the other two providers.
        params = {
            "client_id": settings.apple_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "name email",
            "response_mode": "form_post",
            "state": state,
        }
        return f"https://appleid.apple.com/auth/authorize?{urlencode(params)}"

    raise HTTPException(status_code=400, detail="Fournisseur de connexion inconnu.")


def _apple_client_secret() -> str:
    """Apple doesn't accept a static client secret — it must be a JWT signed
    with the private key generated for this Services ID, re-derivable on
    every request rather than cached (cheap to compute, and avoids ever
    persisting a long-lived signed credential)."""
    now = datetime.now(timezone.utc)
    payload = {
        "iss": settings.apple_team_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "aud": "https://appleid.apple.com",
        "sub": settings.apple_client_id,
    }
    return jwt.encode(
        payload, settings.apple_private_key, algorithm="ES256",
        headers={"kid": settings.apple_key_id},
    )


def exchange_code(provider: str, code: str, redirect_uri: str) -> OAuthProfile:
    """Trades the authorization code for the provider's profile info. Raises
    HTTPException on any failure — callers don't need to distinguish why the
    exchange failed, just that login didn't succeed."""
    _require_configured(provider)

    if provider == "google":
        resp = httpx.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=10)
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Échec de la connexion avec Google.")
        access_token = resp.json().get("access_token")
        info = httpx.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}, timeout=10,
        )
        if info.status_code != 200:
            raise HTTPException(status_code=400, detail="Échec de la connexion avec Google.")
        data = info.json()
        return OAuthProfile(subject=data["sub"], email=data.get("email"), name=data.get("name"))

    if provider == "facebook":
        resp = httpx.get("https://graph.facebook.com/v19.0/oauth/access_token", params={
            "client_id": settings.facebook_client_id,
            "client_secret": settings.facebook_client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        }, timeout=10)
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Échec de la connexion avec Facebook.")
        access_token = resp.json().get("access_token")
        info = httpx.get("https://graph.facebook.com/me", params={
            "fields": "id,name,email", "access_token": access_token,
        }, timeout=10)
        if info.status_code != 200:
            raise HTTPException(status_code=400, detail="Échec de la connexion avec Facebook.")
        data = info.json()
        return OAuthProfile(subject=data["id"], email=data.get("email"), name=data.get("name"))

    if provider == "apple":
        resp = httpx.post("https://appleid.apple.com/auth/token", data={
            "code": code,
            "client_id": settings.apple_client_id,
            "client_secret": _apple_client_secret(),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=10)
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Échec de la connexion avec Apple.")
        id_token = resp.json().get("id_token")
        if not id_token:
            raise HTTPException(status_code=400, detail="Échec de la connexion avec Apple.")
        # Apple's id_token is verified against their published JWKS rather
        # than trusted as-is — anyone could otherwise hand this endpoint a
        # self-signed token claiming to be an arbitrary Apple account.
        jwks_client = jwt.PyJWKClient("https://appleid.apple.com/auth/keys")
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token, signing_key.key, algorithms=["RS256"],
            audience=settings.apple_client_id, issuer="https://appleid.apple.com",
        )
        return OAuthProfile(subject=claims["sub"], email=claims.get("email"), name=None)

    raise HTTPException(status_code=400, detail="Fournisseur de connexion inconnu.")
