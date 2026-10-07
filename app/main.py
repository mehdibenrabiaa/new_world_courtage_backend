import logging
import os
import re
import secrets
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session
from app.auth import hash_password
from app.config import settings
from app.database import Base, engine
from app.models import (
    Lead, LeadContact, Notification, PermissionAction, PermissionResource,
    RolePermission, User, UserRole,
)
from app.routers import (
    leads, contacts, guides, authors, media, questionnaires, consultants, auth, users, permissions, notifications,
    accounts, crm_accounts,
)

# One consistent format for every module's logger (app/email.py already had
# its own; everything else was console.log-style prints or nothing) — a
# timestamp and the logger's module name on every line, since "what broke
# and when" is unreadable without both once there's more than one endpoint
# writing to the same output.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("app")

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
    if "document_upload_token_hash" not in _existing_leads:
        _conn.execute(text("ALTER TABLE leads ADD COLUMN document_upload_token_hash VARCHAR(64)"))
        _conn.commit()
    if "deal_value" not in _existing_leads:
        _conn.execute(text("ALTER TABLE leads ADD COLUMN deal_value FLOAT"))
        _conn.commit()
    if "duplicate_of_id" not in _existing_leads:
        _conn.execute(text(
            "ALTER TABLE leads ADD COLUMN duplicate_of_id INTEGER REFERENCES leads(id) ON DELETE SET NULL"
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
    if "sessions_revoked_at" not in _existing_users:
        _conn.execute(text("ALTER TABLE users ADD COLUMN sessions_revoked_at TIMESTAMP"))
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
    if engine.dialect.name == "postgresql":
        _conn.execute(text("ALTER TABLE users ALTER COLUMN username SET NOT NULL"))
    _conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username_unique ON users (username)"))
    _conn.commit()

# Consultants used to be their own table, loosely linked to a real CRM login
# via an optional user_id — the exact "two different accounts for the same
# person" problem this migration exists to undo. consultant_bookings/
# consultant_unavailabilities now point straight at users.id (see
# models.py), so any pre-existing "consultants" table (from before this
# ran) needs its rows folded into real Users first: one already linked to a
# login just has its bookings/blocks repointed to that user; one with no
# login gets a brand-new consultant-role account created for it (so its
# booking history isn't lost) with an unusable random password until a
# superadmin resets it. One-time per deploy — the table is gone afterward.
if inspect(engine).has_table("consultants"):
    if engine.dialect.name == "postgresql":
        with engine.connect() as _conn:
            _conn.execute(text("ALTER TABLE consultant_bookings DROP CONSTRAINT IF EXISTS consultant_bookings_consultant_id_fkey"))
            _conn.execute(text("ALTER TABLE consultant_unavailabilities DROP CONSTRAINT IF EXISTS consultant_unavailabilities_consultant_id_fkey"))
            _conn.commit()

    with Session(engine) as _session:
        _old_consultants = _session.execute(text("SELECT id, name, email, active, user_id FROM consultants")).all()
        _taken_usernames = {row[0] for row in _session.query(User.username)}
        # Phase 1: resolve every old consultant id to its final new user id
        # (creating an account where needed) *before* touching
        # consultant_bookings/consultant_unavailabilities at all.
        _id_map: dict[int, int] = {}
        for _old_id, _name, _email, _active, _linked_user_id in _old_consultants:
            # Only trust the link if it actually points at a real,
            # role=consultant account — a stale/incorrect link (e.g. onto an
            # admin) would otherwise silently merge this consultant's
            # bookings into an unrelated account and lose their identity.
            _linked_user = _session.get(User, _linked_user_id) if _linked_user_id is not None else None
            if _linked_user is not None and _linked_user.role == UserRole.consultant:
                _id_map[_old_id] = _linked_user.id
                continue
            _base = re.sub(r"[^a-z0-9._-]", "", (_name or "").lower().replace(" ", ".")) or f"consultant{_old_id}"
            _candidate = _base
            _n = 1
            while _candidate in _taken_usernames:
                _n += 1
                _candidate = f"{_base}{_n}"
            _taken_usernames.add(_candidate)
            _new_user = User(
                name=_name or _candidate,
                username=_candidate,
                email=_email or f"{_candidate}@migrated.invalid",
                password_hash=hash_password(secrets.token_urlsafe(24)),
                role=UserRole.consultant,
                active=bool(_active),
            )
            _session.add(_new_user)
            _session.flush()
            _id_map[_old_id] = _new_user.id

        # Phase 2: old consultant ids and the new user ids just created can
        # numerically overlap (both are small sequential integers), so
        # repointing consultant_id in-place old-id-by-old-id risks a later
        # iteration's UPDATE also matching rows an earlier iteration just
        # wrote — moving them a second time onto the wrong account. Every
        # old id is first moved to a negative placeholder (guaranteed to
        # collide with nothing) before any of them land on a real new id.
        for _old_id in _id_map:
            _session.execute(
                text("UPDATE consultant_bookings SET consultant_id = :tmp WHERE consultant_id = :old_id"),
                {"tmp": -_old_id, "old_id": _old_id},
            )
            _session.execute(
                text("UPDATE consultant_unavailabilities SET consultant_id = :tmp WHERE consultant_id = :old_id"),
                {"tmp": -_old_id, "old_id": _old_id},
            )
        for _old_id, _new_id in _id_map.items():
            _session.execute(
                text("UPDATE consultant_bookings SET consultant_id = :new_id WHERE consultant_id = :tmp"),
                {"new_id": _new_id, "tmp": -_old_id},
            )
            _session.execute(
                text("UPDATE consultant_unavailabilities SET consultant_id = :new_id WHERE consultant_id = :tmp"),
                {"new_id": _new_id, "tmp": -_old_id},
            )
        _session.commit()

    with engine.connect() as _conn:
        _conn.execute(text("DROP TABLE consultants"))
        if engine.dialect.name == "postgresql":
            _conn.execute(text(
                "ALTER TABLE consultant_bookings ADD CONSTRAINT consultant_bookings_consultant_id_fkey "
                "FOREIGN KEY (consultant_id) REFERENCES users(id)"
            ))
            _conn.execute(text(
                "ALTER TABLE consultant_unavailabilities ADD CONSTRAINT consultant_unavailabilities_consultant_id_fkey "
                "FOREIGN KEY (consultant_id) REFERENCES users(id) ON DELETE CASCADE"
            ))
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
        _conn.execute(text("ALTER TYPE leadtype ADD VALUE IF NOT EXISTS 'moto'"))
        _conn.execute(text("ALTER TYPE leadtype ADD VALUE IF NOT EXISTS 'auto'"))
        _conn.execute(text("ALTER TYPE leadtype ADD VALUE IF NOT EXISTS 'risques_aggraves'"))
        # "consultants" (reallocating a booked call between consultants,
        # managing consultant profiles/calendars) is a newly added
        # PermissionResource member — same reasoning as leadtype above.
        _conn.execute(text("ALTER TYPE permissionresource ADD VALUE IF NOT EXISTS 'consultants'"))

    # The plain btree indexes on leads.phone/email (see models.py) only
    # speed up exact matches (the duplicate-detection query) — the leads
    # search box does a substring ILIKE, which a btree index can't serve.
    # pg_trgm + a GIN index lets Postgres use an index for that too. SQLite
    # (local dev) has no equivalent extension, so this block is Postgres-only
    # — dev just accepts an unindexed scan, acceptable at dev-DB volumes.
    with engine.connect() as _conn:
        _conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        _conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_leads_name_trgm ON leads USING GIN (name gin_trgm_ops)"
        ))
        _conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_leads_email_trgm ON leads USING GIN (email gin_trgm_ops)"
        ))
        _conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_leads_phone_trgm ON leads USING GIN (phone gin_trgm_ops)"
        ))
        _conn.commit()

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

# No more auto-seeded consultants — a "consultant" is just a User with
# role=consultant now (see the merge migration above), and a fake account
# with no real password/login isn't something worth conjuring on boot. The
# confirmation screen's booking calendar only offers a timeframe once at
# least one active, role=consultant User exists — create one from the CRM's
# Users page to get the public booking flow working on a fresh deploy.

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
    if engine.dialect.name == "postgresql":
        # Postgres enums are a fixed native type — a fresh DB never had
        # 'questionnaires' as a member (it was already gone from the Python
        # enum before this ever ran against Postgres), so comparing the
        # column directly against that literal is a type error. Cast to
        # text first so the comparison is always valid regardless of what
        # the enum currently allows.
        _conn.execute(text("DELETE FROM role_permissions WHERE resource::text = 'questionnaires'"))
    else:
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

# "consultants" was added to PermissionResource after the block above already
# ran on this DB (it only seeds once, when role_permissions is empty) — an
# existing DB has every other resource's rows but none for this one.
# Backfill just this resource's defaults, idempotently (checks its own rows,
# not the table as a whole, so it's a no-op once applied and also covers a
# genuinely fresh DB the same way — no need to duplicate it into the block
# above too).
with Session(engine) as _session:
    _has_consultants_rows = _session.query(RolePermission).filter(
        RolePermission.resource == PermissionResource.consultants
    ).count() > 0
    if not _has_consultants_rows:
        _consultants_defaults: dict[UserRole, set[str]] = {
            UserRole.admin: {a.value for a in PermissionAction},
            UserRole.supervisor: {"view", "create", "edit"},
            UserRole.consultant: set(),
        }
        _session.add_all([
            RolePermission(role=role, resource=PermissionResource.consultants, action=action, allowed=action.value in allowed_actions)
            for role, allowed_actions in _consultants_defaults.items()
            for action in PermissionAction
        ])
        _session.commit()

# Ensure upload directories exist
os.makedirs("uploads/guides", exist_ok=True)
os.makedirs("uploads/authors", exist_ok=True)
os.makedirs("uploads/leads", exist_ok=True)

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


@app.middleware("http")
async def _log_unhandled_errors(request: Request, call_next):
    """FastAPI's default behavior on an unhandled exception is to just
    return a bare 500 with nothing in the server log to say why — this
    logs the full traceback (so a failure is diagnosable from the log
    alone, without reproducing it) while still returning the same generic
    response a client should see."""
    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Une erreur interne est survenue."})
    duration_ms = (time.monotonic() - start) * 1000
    if response.status_code >= 500:
        logger.error("%s %s -> %s (%.0fms)", request.method, request.url.path, response.status_code, duration_ms)
    return response


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
app.include_router(accounts.router, prefix="/api")
app.include_router(crm_accounts.router, prefix="/api")
# No "/api" prefix: matches the CRM's existing lib/api.ts calls for this feature.
app.include_router(questionnaires.router)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


@app.get("/")
def health():
    return {"status": "ok", "service": "new-world-courtage-api"}
