import re
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, EmailStr, field_validator
from app.models import LeadStatus, LeadType, GuideStatus, UserRole, PermissionResource, PermissionAction, AccountType

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
    deal_value: float | None = None
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
    deal_value: float | None = None
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


class TaskLeadRef(BaseModel):
    id: int
    name: str


class LeadTaskWithLeadOut(LeadTaskOut):
    """A task plus just enough about its lead to show in a cross-lead list
    (the "Tâches" sidebar section) — who it's for and a link back to the
    lead, without pulling in everything LeadOut carries."""
    lead: TaskLeadRef
    assigned_to: LeadAssigneeOut | None = None


class LeadDocumentOut(BaseModel):
    id: int
    lead_id: int
    document_label: str
    original_filename: str
    content_type: str | None
    size_bytes: int
    file_url: str
    created_at: datetime

    model_config = {"from_attributes": True}


class LeadDuplicateOut(BaseModel):
    id: int
    name: str

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
    deal_value: float | None = None
    duplicate_of: LeadDuplicateOut | None = None
    assigned_to: LeadAssigneeOut | None = None
    answers: list[LeadAnswerOut] = []
    sticky_notes: list[LeadNoteOut] = []
    tasks: list[LeadTaskOut] = []
    documents: list[LeadDocumentOut] = []
    document_upload_token: str | None = None
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
    deal_value: float | None = None
    duplicate_of: LeadDuplicateOut | None = None
    assigned_to: LeadAssigneeOut | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class LeadActivityOut(BaseModel):
    id: int
    actor_name: str | None
    action: str
    field: str | None
    old_value: str | None
    new_value: str | None
    description: str | None
    created_at: datetime


class ConsultantStatOut(BaseModel):
    consultant_id: int
    name: str
    total_leads: int
    converted_leads: int
    conversion_rate: float
    total_value: float
    converted_value: float


class LeadStatsOut(BaseModel):
    """Aggregate KPIs for the dashboard — computed server-side (a single
    grouped query) instead of the frontend fetching every lead just to
    count them client-side."""
    total_leads: int
    by_status: dict[str, int]
    conversion_rate: float
    total_pipeline_value: float
    converted_value: float
    unread_contacts: int
    by_consultant: list[ConsultantStatOut]


# ── Notifications ────────────────────────────────────────────────────────────

class NotificationOut(BaseModel):
    id: int
    type: str
    message: str
    link: str | None
    read: bool
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
    # A conditional extension rendered within this parent question's block.
    parent_key: str | None = None
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
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshedTokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


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


class ConsultantUnavailabilityOut(BaseModel):
    id: int
    consultant_id: int
    date: str
    time: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConsultantUnavailabilityCreate(BaseModel):
    date: str
    time: str | None = None  # None blocks the whole day


class ConsultantBookingOut(BaseModel):
    """A real, already-confirmed appointment — read-only from the calendar
    editor's point of view (unlike ConsultantUnavailabilityOut, there's no
    "unbook" action here); shown so a consultant/manager sees their actual
    schedule, not just the deliberate blocks."""
    id: int
    date: str
    time: str
    lead_id: int | None
    lead_name: str | None
    created_at: datetime


class ConsultantRef(BaseModel):
    id: int
    name: str


class ConsultantBookingWithConsultantOut(ConsultantBookingOut):
    """A booking plus who it's with — backs the "Rendez-vous" sidebar
    section, which lists bookings across every consultant (for whoever can
    see that) rather than one consultant's calendar at a time."""
    consultant: ConsultantRef


class ConsultantBookingReallocate(BaseModel):
    new_consultant_id: int

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


# ── Public-site accounts (Espace Client / Espace Partenaire) ──────────────────

class AccountRegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    type: AccountType

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Le nom ne peut pas être vide.")
        return v.strip()

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères.")
        return v


class AccountLoginRequest(BaseModel):
    email: EmailStr
    password: str


class AccountOut(BaseModel):
    id: int
    name: str
    email: str
    type: AccountType
    referral_code: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AccountTokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    account: AccountOut


class AccountRefreshRequest(BaseModel):
    refresh_token: str


class AccountRefreshedTokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccountForgotPasswordRequest(BaseModel):
    email: EmailStr


class AccountResetPasswordRequest(BaseModel):
    token: str
    password: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères.")
        return v


class AccountLeadOut(BaseModel):
    """A trimmed-down Lead for the client's own "Espace Client" — just
    enough to show what they submitted and where it stands, none of the
    internal CRM fields (assigned consultant, deal value, notes...)."""
    id: int
    type: LeadType
    status: LeadStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class AccountReferralOut(BaseModel):
    code: str
    link: str


class AccountUpdateRequest(BaseModel):
    """PATCH /accounts/me — name/email both optional so a caller can send
    just the one being changed. Actual password changes go through the
    dedicated change-password endpoint (needs the current password) — but
    an *email* change is just as sensitive (it's what "mot de passe oublié"
    sends the reset link to), so current_password is required here too
    whenever email is being changed on a password-having account. Without
    that, a merely-still-valid access token (e.g. a leaked/stolen one)
    would be enough to silently redirect the account's recovery email and
    take it over — see routers/accounts.py's update_me."""
    name: str | None = None
    email: EmailStr | None = None
    current_password: str | None = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Le nom ne peut pas être vide.")
        return v.strip() if v else v


class AccountChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères.")
        return v


class AdminAccountOut(BaseModel):
    """CRM-facing view of a public-site account (routers/crm_accounts.py) —
    adds the staff-only fields AccountOut deliberately leaves out
    (active, oauth_provider) plus a computed leads_count, since "how many
    devis has this client actually submitted" is the first thing a
    consultant looking at this list wants to know."""
    id: int
    name: str
    email: str
    type: AccountType
    referral_code: str | None
    active: bool
    oauth_provider: str | None
    leads_count: int
    created_at: datetime


class AdminAccountDetailOut(AdminAccountOut):
    leads: list[AccountLeadOut]
