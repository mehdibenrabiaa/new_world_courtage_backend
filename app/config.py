from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./nwc.db"

    # Some hosts (Heroku-style, including a lot of Postgres-as-a-service
    # UIs) hand out "postgres://" connection strings, but SQLAlchemy 1.4+
    # only recognizes the "postgresql://" dialect name — normalize here so
    # whatever gets pasted into DATABASE_URL just works.
    @field_validator("database_url")
    @classmethod
    def _normalize_postgres_scheme(cls, v: str) -> str:
        if v.startswith("postgres://"):
            return "postgresql://" + v[len("postgres://"):]
        return v
    allowed_origins: str = "http://localhost:3000,http://localhost:3001,http://localhost:3002,https://crm.newworldcourtage.fr,https://newworldcourtage.fr,https://www.newworldcourtage.fr"
    secret_key: str = "change-me-in-production"
    base_url: str = "http://localhost:8000"

    # Outbound email (SMTP) — all optional. Left blank, app/email.py no-ops
    # instead of sending, so lead/booking creation keeps working before
    # these are filled in (see .env.example).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    email_from: str = "New World Courtage <devis@newworldcourtage.com>"

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]

    class Config:
        env_file = ".env"


settings = Settings()
