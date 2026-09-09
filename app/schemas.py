import re
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, EmailStr, field_validator
from app.models import LeadStatus, LeadType, GuideStatus, UserRole, PermissionResource, PermissionAction

NoteColor = Literal["yellow", "pink", "blue", "green", "purple", "orange"]


# ── Leads ─────────────────────────────────────────────────────────────────────

class LeadAnswerIn(BaseModel):
    catalog_key: str
    question: str
    value: str


class LeadAnswerOut(BaseModel):
    id: int
    catalog_key: str
    question: str
    value: str

    model_config = {"from_attributes": True}


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
    # Only ever set by the CRM (a consultant creating a lead manually
    # auto-assigns it to themselves so they can still see it afterward —
    # see routers/leads.py). The public site's own submissions never send
    # this, so it defaults to unassigned.
    assigned_to_id: int | None = None
    answers: list[LeadAnswerIn] = []

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
    # Reassignment — stripped out server-side unless the caller is
    # superadmin/admin (see update_lead). 0 is not a valid user id, so it's
    # used as an explicit "unassign" sentinel distinct from "field omitted";
    # None here just means "don't touch this field".
    assigned_to_id: int | None = None
    unassign: bool = False


class LeadNoteCreate(BaseModel):
    content: str
    color: NoteColor = "yellow"


class LeadNoteUpdate(BaseModel):
    content: str | None = None
    color: NoteColor | None = None


class LeadNoteOut(BaseModel):
    id: int
    content: str
    color: str
    created_at: datetime

    model_config = {"from_attributes": True}


class LeadTaskCreate(BaseModel):
    comment: str
    action: str
    due_date: str


class LeadTaskUpdate(BaseModel):
    comment: str | None = None
    action: str | None = None
    due_date: str | None = None
    completed: bool | None = None


class LeadTaskOut(BaseModel):
    id: int
    comment: str
    action: str
    due_date: str
    completed: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class LeadContactOut(BaseModel):
    id: int
    lead_id: int | None
    # True when the source lead is gone — either hard-deleted (lead_id itself
    # went NULL) or soft-deleted via DELETE /leads/{id} (Lead.deleted, which
    # never touches lead_id — see routers/leads.py's delete_lead).
    lead_deleted: bool
    name: str
    phone: str
    email: str | None
    address: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class LeadAssigneeOut(BaseModel):
    id: int
    name: str
    email: str

    model_config = {"from_attributes": True}


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
    assigned_to: LeadAssigneeOut | None = None
    answers: list[LeadAnswerOut] = []
    sticky_notes: list[LeadNoteOut] = []
    tasks: list[LeadTaskOut] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LeadListOut(BaseModel):
    """What the leads table actually displays — unlike LeadOut, this
    deliberately skips answers/sticky_notes/tasks (and the other detail-only
    fields) so listing a page of leads doesn't lazy-load three relationships
    per row for content the list view never renders."""
    id: int
    type: LeadType
    status: LeadStatus
    name: str
    phone: str
    email: str | None
    assigned_to: LeadAssigneeOut | None = None
    created_at: datetime

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


# ── Questionnaires ────────────────────────────────────────────────────────────

class QuestionnaireCreate(BaseModel):
    slug: str
    name: str

    @field_validator("slug")
    @classmethod
    def slug_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Le slug est requis.")
        return v.strip()

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Le nom est requis.")
        return v.strip()


class QuestionnaireUpdate(BaseModel):
    slug: str | None = None
    name: str | None = None

    @field_validator("slug")
    @classmethod
    def slug_not_empty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Le slug est requis.")
        return v.strip() if v is not None else v

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Le nom est requis.")
        return v.strip() if v is not None else v


class CatalogOptionOut(BaseModel):
    label: str
    value: str


class CatalogEntryOut(BaseModel):
    """A question available to add, from app/question_catalog.py — its
    default wording before any override, plus its fixed type/options."""
    key: str
    section: str | None = None
    eyebrow: str | None = None
    type: str
    input_type: str | None = None
    question: str
    hint: str | None = None
    placeholder: str | None = None
    required: bool = True
    card: bool = False
    options: list[CatalogOptionOut] = []


class QuestionAdd(BaseModel):
    catalog_key: str
    order: int = 0


class QuestionWordingUpdate(BaseModel):
    question: str | None = None
    hint: str | None = None
    placeholder: str | None = None
    order: int | None = None


class RuleOut(BaseModel):
    """Conditional-visibility rule, resolved from a catalog entry's
    `skip_unless` against sibling questions in the same questionnaire (see
    routers/questionnaires.py's _merge). Consumed by the public site's
    isStepSkipped()."""
    source_question_id: int
    operator: str
    value: str
    action: str = "skip"


class QuestionOut(BaseModel):
    """A question included in a questionnaire, with catalog + override
    wording already merged (see routers/questionnaires.py's _merge)."""
    id: int
    questionnaire_id: int
    catalog_key: str
    key: str  # alias of catalog_key — matches what the public site's mapQuestionToStep expects
    section: str | None
    eyebrow: str | None
    type: str
    input_type: str | None
    unit: str | None = None
    question: str
    hint: str | None
    placeholder: str | None
    required: bool
    card: bool
    # A "gate" question is shown on its own screen before the step-by-step
    # wizard begins (not one of its sections/tabs) — see CarInsuranceForm.js.
    gate: bool = False
    # Restricts this question to prospects who picked at least one of these
    # products on the gate screen (see the "produits_interesses" gate
    # question) — None/omitted means it applies to every product.
    products: list[str] | None = None
    order: int
    options: list[CatalogOptionOut]
    rules: list[RuleOut] = []
    # A question whose catalog entry no longer exists (catalog edited/removed
    # after it was added) — surfaced so the CRM can flag it instead of crashing.
    orphaned: bool = False


class QuestionnaireOut(BaseModel):
    id: int
    slug: str
    name: str
    questions: list[QuestionOut]

    model_config = {"from_attributes": True}


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    name: str
    username: str
    email: str
    role: UserRole
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class MeOut(UserOut):
    """GET /api/auth/me's response — UserOut plus this user's own resolved
    permissions (role -> {resource: [allowed actions]}), so the CRM can gate
    its UI (e.g. hide a Delete button) without a second round-trip. A
    superadmin gets every resource/action listed as allowed, computed here
    rather than stored, since superadmin is never in role_permissions."""
    permissions: dict[str, list[str]]


class UserCreate(BaseModel):
    name: str
    username: str
    email: str
    password: str
    role: UserRole = UserRole.consultant

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Le nom est requis.")
        return v.strip()

    @field_validator("username")
    @classmethod
    def username_normalize(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("Le nom d'utilisateur est requis.")
        if not re.fullmatch(r"[a-z0-9._-]+", v):
            raise ValueError("Le nom d'utilisateur ne peut contenir que des lettres, chiffres, points, tirets et underscores.")
        return v

    @field_validator("email")
    @classmethod
    def email_normalize(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("L'email est requis.")
        return v

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères.")
        return v


class UserUpdate(BaseModel):
    name: str | None = None
    username: str | None = None
    role: UserRole | None = None
    active: bool | None = None
    password: str | None = None

    @field_validator("username")
    @classmethod
    def username_normalize(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if not v or not re.fullmatch(r"[a-z0-9._-]+", v):
            raise ValueError("Nom d'utilisateur invalide.")
        return v

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str | None) -> str | None:
        if v is not None and len(v) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères.")
        return v


class RolePermissionOut(BaseModel):
    role: UserRole
    resource: PermissionResource
    action: PermissionAction
    allowed: bool

    model_config = {"from_attributes": True}


class RolePermissionUpdate(BaseModel):
    allowed: bool


# ── Consultants / booking ────────────────────────────────────────────────────

class SlotOut(BaseModel):
    time: str
    available: bool


class AvailabilityOut(BaseModel):
    date: str
    slots: list[SlotOut]


class BookingCreate(BaseModel):
    date: str
    time: str
    lead_id: int | None = None


class BookingOut(BaseModel):
    id: int
    date: str
    time: str
    consultant_name: str
    created_at: datetime

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
