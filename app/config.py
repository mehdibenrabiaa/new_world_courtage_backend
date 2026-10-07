from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./nwc.db"

    # Hosts hand out Postgres connection strings in several forms
    # ("postgres://", "postgresql://", "postgresql+psycopg://" for psycopg 3…).
    # The only driver installed is psycopg2 (requirements.txt), so every
    # Postgres form is pointed at it — whatever gets pasted into DATABASE_URL
    # just works instead of crashing at startup on a missing driver.
    @field_validator("database_url")
    @classmethod
    def _normalize_postgres_scheme(cls, v: str) -> str:
        v = v.strip()
        scheme, sep, rest = v.partition("://")
        if sep and scheme in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2", "postgresql+psycopg3"}:
            return "postgresql+psycopg2://" + rest
        return v
    allowed_origins: str = "http://localhost:3000,http://localhost:3001,http://localhost:3002,https://crm.newworldcourtage.fr,https://newworldcourtage.fr,https://www.newworldcourtage.fr"
    secret_key: str = "change-me-in-production"
    base_url: str = "http://localhost:8000"
    # Where account-facing links point (password reset emails, OAuth
    # callback redirects back to the site) — the public marketing/quote
    # site, not the CRM.
    frontend_url: str = "http://localhost:3000"

    # Social sign-in for public-site customer/partner accounts (app/accounts
    # feature) — all optional, same "blank = not offered yet" pattern as
    # SMTP above. Each provider's /accounts/oauth/{provider}/start route
    # 501s with a clear message until its client id/secret are filled in.
    google_client_id: str = ""
    google_client_secret: str = ""
    # Apple's "Sign in with Apple" uses a Services ID as the client id, plus
    # a private key (Team ID + Key ID) to sign a client secret JWT per
    # request rather than a static secret string.
    apple_client_id: str = ""
    apple_team_id: str = ""
    apple_key_id: str = ""
    apple_private_key: str = ""
    facebook_client_id: str = ""
    facebook_client_secret: str = ""

    # Outbound email (SMTP) — all optional. Left blank, app/email.py no-ops
    # instead of sending, so lead/booking creation keeps working before
    # these are filled in (see .env.example).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    email_from: str = "New World Courtage <devis@newworldcourtage.com>"

    # IMAP — not read anywhere yet (nothing checks the inbox today); the
    # mailbox's IMAP credentials are kept here so they're declared in one
    # place if/when that's built, rather than only living in .env.
    mail_imap_host: str = ""
    mail_imap_port: int = 993

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]

    class Config:
        env_file = ".env"


settings = Settings()
