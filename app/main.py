import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text, inspect
from app.config import settings
from app.database import Base, engine
from app.routers import leads, contacts, guides

# Create tables on startup
Base.metadata.create_all(bind=engine)

# Add columns that were introduced after the initial table creation
with engine.connect() as _conn:
    _existing = [c["name"] for c in inspect(engine).get_columns("guides")]
    if "image_url" not in _existing:
        _conn.execute(text("ALTER TABLE guides ADD COLUMN image_url TEXT"))
        _conn.commit()

# Ensure upload directory exists
os.makedirs("uploads/guides", exist_ok=True)

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
)

app.include_router(leads.router, prefix="/api")
app.include_router(contacts.router, prefix="/api")
app.include_router(guides.router, prefix="/api")

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


@app.get("/")
def health():
    return {"status": "ok", "service": "new-world-courtage-api"}
