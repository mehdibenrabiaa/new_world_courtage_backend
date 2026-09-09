import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session
from app.config import settings
from app.database import Base, engine
from app.models import Consultant, Lead, LeadContact
from app.routers import leads, contacts, guides, authors, media, questionnaires, consultants, auth

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

    _existing_tasks = [c["name"] for c in inspect(engine).get_columns("lead_tasks")]
    if "completed" not in _existing_tasks:
        _conn.execute(text("ALTER TABLE lead_tasks ADD COLUMN completed BOOLEAN NOT NULL DEFAULT FALSE"))
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
app.include_router(leads.router, prefix="/api")
app.include_router(contacts.router, prefix="/api")
app.include_router(guides.router, prefix="/api")
app.include_router(authors.router, prefix="/api")
app.include_router(media.router, prefix="/api")
app.include_router(consultants.router, prefix="/api")
# No "/api" prefix: matches the CRM's existing lib/api.ts calls for this feature.
app.include_router(questionnaires.router)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


@app.get("/")
def health():
    return {"status": "ok", "service": "new-world-courtage-api"}
