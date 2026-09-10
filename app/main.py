import os
import re
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session
from app.config import settings
from app.database import Base, engine
from app.models import (
    Consultant, Lead, LeadContact, Notification, PermissionAction, PermissionResource,
    RolePermission, User, UserRole,
)
from app.routers import (
    leads, contacts, guides, authors, media, questionnaires, consultants, auth, users, permissions, notifications,
)

# The questionnaire feature moved from a fully dynamic question model
# (type/options/rules/draft-publish, all admin-defined) to a fixed catalog
# (app/question_catalog.py) that the CRM can only include/exclude/reword —
# the old questions/options/rules tables are incompatible with the new
# schema, so drop and let create_all below rebuild them. One-time per
# deploy: subsequent restarts see the new columns and skip this.
with engine.connect() as _conn:
    if inspect(engine).has_table("questions"):
        _existing_question_cols = [c["name"] for c in inspect(engine).get_columns("questions")]
        if "draft_of_id" in _existing_question_cols:
            _conn.execute(text("DROP TABLE IF EXISTS rules"))
            _conn.execute(text("DROP TABLE IF EXISTS options"))
            _conn.execute(text("DROP TABLE IF EXISTS questions"))
            _conn.commit()

# Create tables on startup
Base.metadata.create_all(bind=engine)

# Add columns that were introduced after the initial table creation
with engine.connect() as _conn:
    _existing = [c["name"] for c in inspect(engine).get_columns("guides")]
    if "image_url" not in _existing:
        _conn.execute(text("ALTER TABLE guides ADD COLUMN image_url TEXT"))
        _conn.commit()

    _existing_leads = [c["name"] for c in inspect(engine).get_columns("leads")]
    if "deleted" not in _existing_leads:
        _conn.execute(text("ALTER TABLE leads ADD COLUMN deleted BOOLEAN NOT NULL DEFAULT FALSE"))
        _conn.commit()
    if "assigned_to_id" not in _existing_leads:
        _conn.execute(text(
            "ALTER TABLE leads ADD COLUMN assigned_to_id INTEGER REFERENCES users(id) ON DELETE SET NULL"
        ))
        _conn.commit()

    _existing_tasks = [c["name"] for c in inspect(engine).get_columns("lead_tasks")]
    if "completed" not in _existing_tasks:
        _conn.execute(text("ALTER TABLE lead_tasks ADD COLUMN completed BOOLEAN NOT NULL DEFAULT FALSE"))
        _conn.commit()

    # "users" pre-dates the role-based-access-control feature — role_permissions
    # is a brand-new table so create_all above already created the Postgres
    # `userrole` enum type for it; reuse that same type here.
    _existing_users = [c["name"] for c in inspect(engine).get_columns("users")]
    if "role" not in _existing_users:
        _conn.execute(text("ALTER TABLE users ADD COLUMN role userrole NOT NULL DEFAULT 'consultant'"))
        _conn.commit()
    if "username" not in _existing_users:
        _conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR(60)"))
        _conn.commit()

# Login switched from email to username. Backfill one for any account
# created before this (derived from their email's local part, de-duplicated
# against everyone else's), then lock the column down now that every row
# has one — safe to run every boot, both statements are no-ops once done.
with Session(engine) as _session:
    _users_missing_username = _session.query(User).filter(User.username.is_(None)).all()
    if _users_missing_username:
        _taken_usernames = {
            row[0] for row in _session.query(User.username).filter(User.username.isnot(None))
        }
        for _user in _users_missing_username:
            _base = re.sub(r"[^a-z0-9._-]", "", _user.email.split("@")[0].lower()) or f"user{_user.id}"
            _candidate = _base
            _n = 1
            while _candidate in _taken_usernames:
                _n += 1
                _candidate = f"{_base}{_n}"
            _user.username = _candidate
            _taken_usernames.add(_candidate)
        _session.commit()

with engine.connect() as _conn:
    _conn.execute(text("ALTER TABLE users ALTER COLUMN username SET NOT NULL"))
    _conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username_unique ON users (username)"))
    _conn.commit()

# Postgres enums are a fixed native type — adding a Python enum member (e.g.
# LeadType.garage) doesn't add it to the existing DB type, so inserts with
# the new value would fail until this runs. SQLAlchemy's Enum stores the
# member *name* ("garage"), not its .value ("Assurance Garage") — matching
# how every other LeadType member is already stored here. ALTER TYPE ... ADD
# VALUE must run outside a transaction block, hence AUTOCOMMIT.
if engine.dialect.name == "postgresql":
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as _conn:
        _conn.execute(text("ALTER TYPE leadtype ADD VALUE IF NOT EXISTS 'garage'"))

# Backfill: lead_contacts is a new table (added after leads already existed),
# so give every pre-existing lead a matching contact snapshot on first boot.
with Session(engine) as _session:
    _existing_contact_lead_ids = {row[0] for row in _session.query(LeadContact.lead_id).filter(LeadContact.lead_id.isnot(None))}
    _leads_missing_contact = _session.query(Lead).filter(~Lead.id.in_(_existing_contact_lead_ids)).all() if _existing_contact_lead_ids else _session.query(Lead).all()
    for _lead in _leads_missing_contact:
        _address = next((a.value for a in _lead.answers if "adresse" in a.catalog_key), None)
        _session.add(LeadContact(
            lead_id=_lead.id, name=_lead.name, phone=_lead.phone,
            email=_lead.email, address=_address, created_at=_lead.created_at,
        ))
    if _leads_missing_contact:
        _session.commit()

# Seed a couple of consultants on first boot — the confirmation screen's
# booking calendar only ever offers a timeframe when at least one active
# consultant has no booking there, so it needs at least one row to work at all.
with Session(engine) as _session:
    if _session.query(Consultant).count() == 0:
        _session.add_all([
            Consultant(name="Sophie Martin"),
            Consultant(name="Julien Bernard"),
        ])
        _session.commit()

# Bootstrap: someone has to be able to log in and configure the permissions
# matrix in the first place. If no superadmin exists yet (a brand new DB, or
# one where the only account pre-dates roles and got the "consultant"
# column default), promote whoever has the oldest account.
with Session(engine) as _session:
    if _session.query(User).filter(User.role == UserRole.superadmin).count() == 0:
        _first_user = _session.query(User).order_by(User.id.asc()).first()
        if _first_user:
            _first_user.role = UserRole.superadmin
            _session.commit()

# "questionnaires" was removed from PermissionResource (editing them is
# superadmin-only now, not a grantable permission — see
# routers/questionnaires.py) — drop any rows already seeded for it so
# SQLAlchemy's Enum column never has to deserialize a value with no
# matching Python member.
with engine.connect() as _conn:
    _conn.execute(text("DELETE FROM role_permissions WHERE resource = 'questionnaires'"))
    _conn.commit()

# Seed default role permissions on first boot — a superadmin can change any
# of this afterward from the CRM's Permissions page. superadmin itself is
# never seeded here: it always passes every check (see app/auth.py).
with Session(engine) as _session:
    if _session.query(RolePermission).count() == 0:
        _all_actions = {a.value for a in PermissionAction}
        _defaults: dict[UserRole, dict[PermissionResource, set[str]]] = {
            UserRole.admin: {r: set(_all_actions) for r in PermissionResource},
            UserRole.supervisor: {r: {"view", "create", "edit"} for r in PermissionResource},
            UserRole.consultant: {
                PermissionResource.leads: {"view", "create", "edit"},
                PermissionResource.contacts: {"view"},
                PermissionResource.guides: set(),
                PermissionResource.authors: set(),
                PermissionResource.media: set(),
            },
        }
        _rows = [
            RolePermission(role=role, resource=resource, action=action, allowed=action.value in allowed_actions)
            for role, resource_map in _defaults.items()
            for resource, allowed_actions in resource_map.items()
            for action in PermissionAction
        ]
        _session.add_all(_rows)
        _session.commit()

# Ensure upload directories exist
os.makedirs("uploads/guides", exist_ok=True)
os.makedirs("uploads/authors", exist_ok=True)

app = FastAPI(
    title="New World Courtage API",
    description="Backend API for leads and contacts management.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Browser JS can't read a response header via fetch() unless it's
    # explicitly exposed, even same-origin-looking ones like this pagination
    # count (see routers/leads.py's list_leads).
    expose_headers=["X-Total-Count"],
    # Chromium's Private Network Access check treats a page on one localhost
    # port calling another (e.g. the site on :3000 calling this API on :8000)
    # as a public->private-network request and preflights it separately —
    # without this, Starlette's CORSMiddleware itself rejects that preflight
    # with 400 "Disallowed CORS private-network", which surfaces in the
    # browser as a generic "Failed to fetch" (curl doesn't send this
    # preflight header at all, so it never reproduces the failure).
    allow_private_network=True,
)

app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(permissions.router, prefix="/api")
app.include_router(leads.router, prefix="/api")
app.include_router(contacts.router, prefix="/api")
app.include_router(guides.router, prefix="/api")
app.include_router(authors.router, prefix="/api")
app.include_router(media.router, prefix="/api")
app.include_router(consultants.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
# No "/api" prefix: matches the CRM's existing lib/api.ts calls for this feature.
app.include_router(questionnaires.router)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


@app.get("/")
def health():
    return {"status": "ok", "service": "new-world-courtage-api"}
