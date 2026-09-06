import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text, inspect
from app.config import settings
from app.database import Base, engine
from app.routers import leads, contacts, guides, authors, media, questionnaires

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

# Postgres enums are a fixed native type — adding a Python enum member (e.g.
# LeadType.garage) doesn't add it to the existing DB type, so inserts with
# the new value would fail until this runs. SQLAlchemy's Enum stores the
# member *name* ("garage"), not its .value ("Assurance Garage") — matching
# how every other LeadType member is already stored here. ALTER TYPE ... ADD
# VALUE must run outside a transaction block, hence AUTOCOMMIT.
with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as _conn:
    _conn.execute(text("ALTER TYPE leadtype ADD VALUE IF NOT EXISTS 'garage'"))

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

app.include_router(leads.router, prefix="/api")
app.include_router(contacts.router, prefix="/api")
app.include_router(guides.router, prefix="/api")
app.include_router(authors.router, prefix="/api")
app.include_router(media.router, prefix="/api")
# No "/api" prefix: matches the CRM's existing lib/api.ts calls for this feature.
app.include_router(questionnaires.router)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


@app.get("/")
def health():
    return {"status": "ok", "service": "new-world-courtage-api"}
