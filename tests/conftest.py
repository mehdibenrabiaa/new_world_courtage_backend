"""Test setup shared by every test module.

app/main.py isn't just route registration — importing it runs every
migration/seed block at module level against whatever `app.database.engine`
resolves to (creating tables, seeding role_permissions, etc.). That's the
real app's boot sequence, so tests reuse it wholesale rather than
duplicating schema/seed logic — but it means DATABASE_URL must point at a
throwaway file *before* app.main (or anything importing app.database) is
ever imported, or tests would run their migrations against the real dev
DB (nwc.db).

Isolation between tests: a SAVEPOINT/nested-transaction fixture was tried
first (the usual SQLAlchemy test recipe) but fights pysqlite's own
implicit-transaction handling once mixed with app.main's many separate
`with engine.connect(): ...; conn.commit()` migration blocks — one of them
was left holding a transaction open, so a later test's explicit BEGIN
failed with "cannot start a transaction within a transaction". Wiping the
mutable tables after each test is less elegant but doesn't depend on
getting that interaction right."""

import os
import tempfile

import pytest

_TEST_DB_FD, _TEST_DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_TEST_DB_FD)
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"
# Force app/email.py to no-op regardless of what a real .env has configured
# — a test run must never send a real email.
os.environ["SMTP_HOST"] = ""

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.database import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User, UserRole  # noqa: E402

# Every table a test could conceivably write to — cleared after each test
# so the next one starts from the same clean baseline (just whatever
# app.main seeded on first boot: role_permissions, and nothing else).
# role_permissions itself is deliberately NOT listed: it's the seeded
# baseline every test's permission checks rely on, and no test mutates it.
_MUTABLE_TABLES = [
    "refresh_tokens", "notifications", "lead_activities", "lead_documents",
    "lead_tasks", "lead_notes", "lead_answers", "lead_contacts", "leads",
    "consultant_bookings", "consultant_unavailabilities", "contacts", "users",
]


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_db():
    yield
    try:
        os.remove(_TEST_DB_PATH)
    except OSError:
        pass


@pytest.fixture()
def db_session():
    with Session(engine) as session:
        yield session
    with engine.connect() as conn:
        for table in _MUTABLE_TABLES:
            conn.execute(text(f"DELETE FROM {table}"))
        conn.commit()


@pytest.fixture()
def client(db_session):
    return TestClient(app)


def make_user(db_session: Session, *, username: str, role: UserRole, password: str = "testpass123", active: bool = True) -> User:
    user = User(
        name=username.title(), username=username, email=f"{username}@test.invalid",
        password_hash=hash_password(password), role=role, active=active,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def login(client: TestClient, username: str, password: str = "testpass123") -> dict:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
