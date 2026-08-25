from datetime import datetime
from typing import Any
from pydantic import BaseModel, EmailStr, field_validator
from app.models import LeadStatus, LeadType, GuideStatus


# ── Leads ─────────────────────────────────────────────────────────────────────

class LeadCreate(BaseModel):
    type: LeadType
    name: str
    phone: str
    email: str | None = None
    immat: str | None = None
    naissance: str | None = None
    permis: str | None = None
    siret: str | None = None
    activite: str | None = None
    source: str | None = None

    @field_validator("phone")
    @classmethod
    def phone_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Le numéro de téléphone est requis.")
        return v.strip()


class LeadUpdate(BaseModel):
    status: LeadStatus | None = None
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    type: LeadType | None = None
    immat: str | None = None
    naissance: str | None = None
    permis: str | None = None
    siret: str | None = None
    activite: str | None = None
    notes: str | None = None


class LeadOut(BaseModel):
    id: int
    type: LeadType
    status: LeadStatus
    name: str
    phone: str
    email: str | None
    immat: str | None
    naissance: str | None
    permis: str | None
    siret: str | None
    activite: str | None
    source: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Guides ────────────────────────────────────────────────────────────────────

class GuideCreate(BaseModel):
    title: str
    slug: str
    category: str
    status: GuideStatus = GuideStatus.brouillon
    category_href: str | None = None
    intro: str | None = None
    author_name: str | None = None
    author_avatar: str | None = None
    editor_name: str | None = None
    reviewer_name: str | None = None
    updated_date: str | None = None
    reading_time: str | None = None
    image_url: str | None = None
    blocks: list[Any] = []


class GuideUpdate(BaseModel):
    title: str | None = None
    slug: str | None = None
    category: str | None = None
    status: GuideStatus | None = None
    category_href: str | None = None
    intro: str | None = None
    author_name: str | None = None
    author_avatar: str | None = None
    editor_name: str | None = None
    reviewer_name: str | None = None
    updated_date: str | None = None
    reading_time: str | None = None
    image_url: str | None = None
    blocks: list[Any] | None = None


class GuideOut(BaseModel):
    id: int
    title: str
    slug: str
    category: str
    status: GuideStatus
    category_href: str | None
    intro: str | None
    author_name: str | None
    author_avatar: str | None
    editor_name: str | None
    reviewer_name: str | None
    updated_date: str | None
    reading_time: str | None
    image_url: str | None
    blocks: list[Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("blocks", mode="before")
    @classmethod
    def coerce_blocks(cls, v: Any) -> list:
        return v if v is not None else []


# ── Authors ───────────────────────────────────────────────────────────────────

class AuthorCreate(BaseModel):
    name: str
    avatar_url: str | None = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Le nom de l'auteur est requis.")
        return v.strip()


class AuthorUpdate(BaseModel):
    name: str | None = None
    avatar_url: str | None = None


class AuthorOut(BaseModel):
    id: int
    name: str
    avatar_url: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Contacts ──────────────────────────────────────────────────────────────────

class ContactCreate(BaseModel):
    name: str
    email: str
    phone: str | None = None
    subject: str | None = None
    message: str

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Le message ne peut pas être vide.")
        return v.strip()


class ContactOut(BaseModel):
    id: int
    name: str
    email: str
    phone: str | None
    subject: str | None
    message: str
    read: bool
    created_at: datetime

    model_config = {"from_attributes": True}
