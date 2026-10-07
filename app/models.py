from datetime import datetime, timezone
from typing import Any
from sqlalchemy import String, Text, DateTime, JSON, Enum as SAEnum, ForeignKey, UniqueConstraint, Index, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
import enum


class UserRole(str, enum.Enum):
    """A fixed staff hierarchy, not user-definable roles. superadmin always
    has every permission (hardcoded, never stored in role_permissions) —
    the other three are configurable via the permissions matrix, editable
    only by a superadmin (see app/routers/permissions.py)."""
    superadmin = "superadmin"
    admin = "admin"
    supervisor = "supervisor"
    consultant = "consultant"


class PermissionResource(str, enum.Enum):
    """Questionnaires are deliberately not here — editing them is
    superadmin-only, hardcoded (see routers/questionnaires.py), not a
    permission any role can be granted."""
    leads = "leads"
    contacts = "contacts"
    guides = "guides"
    authors = "authors"
    media = "media"
    consultants = "consultants"


class PermissionAction(str, enum.Enum):
    view = "view"
    create = "create"
    edit = "edit"
    delete = "delete"


class LeadStatus(str, enum.Enum):
    new = "new"
    contacted = "contacted"
    qualified = "qualified"
    converted = "converted"
    lost = "lost"


class LeadType(str, enum.Enum):
    """A lead's category — matches the guide categories used across the site
    (see the CRM's lib/categories.ts, which is the source of truth for the
    exact wording).

    The CRM only offers the products sold today (auto, moto, risques
    aggravés, taxi, VTC, garage, général); the others stay here so leads
    created with them before still load."""
    auto = "Assurance Auto"
    moto = "Assurance Moto"
    risques_aggraves = "Assurance Risques aggravés"
    taxi = "Assurance Taxi"
    vtc = "Assurance VTC"
    garage = "Assurance Garage"
    general = "Assurance Général"
    # No longer sold:
    flotte_transport = "Assurance Flotte & Transport"
    ambulance = "Assurance Ambulance"
    pro_auto = "Assurance Pro de l'auto"
    construction = "Assurance Construction"
    immobilier = "Assurance Immobilier"


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        # Plain btree indexes — help the exact-match duplicate-detection
        # query on every write and (on Postgres) the ILIKE search on every
        # read; on SQLite they still speed up the exact-match half. A real
        # substring-search speedup (GIN + pg_trgm) is added separately in
        # main.py's Postgres-only migration block, since it needs an
        # extension SQLite has no equivalent for.
        Index("ix_leads_phone", "phone"),
        Index("ix_leads_email", "email"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    type: Mapped[LeadType] = mapped_column(SAEnum(LeadType))
    status: Mapped[LeadStatus] = mapped_column(SAEnum(LeadStatus), default=LeadStatus.new)

    # Contact info
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Vehicle lead fields
    immat: Mapped[str | None] = mapped_column(String(20), nullable=True)
    naissance: Mapped[str | None] = mapped_column(String(10), nullable=True)  # MM/YYYY
    permis: Mapped[str | None] = mapped_column(String(10), nullable=True)    # MM/YYYY

    # Business lead fields
    siret: Mapped[str | None] = mapped_column(String(20), nullable=True)
    activite: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Meta
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)  # page URL or campaign
    # Estimated/actual premium value — optional, set by a consultant once
    # they have a figure. Powers pipeline-value reporting; NULL just means
    # "not estimated yet", not zero.
    deal_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Set at creation if an existing lead already shares this phone or email
    # (see create_lead) — a soft flag, not a block, since the public form
    # shouldn't refuse a legitimate resubmission. Self-referential, ON
    # DELETE SET NULL so removing the original doesn't cascade.
    duplicate_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    # "Deleting" a lead from the CRM only hides it (excluded from list_leads) —
    # it stays in the DB so nothing is ever lost to an accidental click.
    deleted: Mapped[bool] = mapped_column(default=False)
    # Who's working this lead — a consultant only ever sees/acts on leads
    # assigned to them (enforced in routers/leads.py), and only superadmin/
    # admin can set this. NULL means unassigned (the default for every
    # public-site submission, which never sets it). ON DELETE SET NULL so
    # removing a staff account doesn't take their leads down with them.
    assigned_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    document_upload_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    answers: Mapped[list["LeadAnswer"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="LeadAnswer.id"
    )
    sticky_notes: Mapped[list["LeadNote"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="LeadNote.created_at.desc()"
    )
    tasks: Mapped[list["LeadTask"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="LeadTask.due_date"
    )
    documents: Mapped[list["LeadDocument"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="LeadDocument.created_at.desc()"
    )
    activity: Mapped[list["LeadActivity"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="LeadActivity.created_at.desc()",
        foreign_keys="LeadActivity.lead_id",
    )
    assigned_to: Mapped["User | None"] = relationship(foreign_keys=[assigned_to_id], passive_deletes=True)
    duplicate_of: Mapped["Lead | None"] = relationship(remote_side=[id], foreign_keys=[duplicate_of_id])


class LeadAnswer(Base):
    """One questionnaire answer attached to a lead — catalog_key/question are
    snapshotted at submission time (see app/question_catalog.py) so they stay
    correct even if the catalog entry is later reworded or removed."""

    __tablename__ = "lead_answers"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"))
    catalog_key: Mapped[str] = mapped_column(String(120))
    question: Mapped[str] = mapped_column(String(500))
    value: Mapped[str] = mapped_column(Text)

    lead: Mapped["Lead"] = relationship(back_populates="answers")


class LeadContact(Base):
    """A snapshot of a lead's contact info (name/phone/email/address), kept
    in its own table linked to the lead by a nullable FK. Unlike
    answers/notes/tasks this has no cascade — if the lead row is ever hard-
    deleted, `lead_id` just goes NULL (ON DELETE SET NULL) instead of the
    contact disappearing with it, since the whole point is that this survives
    independently of the lead's lifecycle."""

    __tablename__ = "lead_contacts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(120), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    lead: Mapped["Lead | None"] = relationship(passive_deletes=True)


NOTE_COLORS = ("yellow", "pink", "blue", "green", "purple", "orange")


class LeadNote(Base):
    """A free-form sticky note attached to a lead — the CRM lets a lead have
    any number of these (unlike the old single `notes` text field it
    replaces), each with its own color and creation timestamp."""

    __tablename__ = "lead_notes"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"))
    content: Mapped[str] = mapped_column(Text)
    color: Mapped[str] = mapped_column(String(20), default="yellow")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    lead: Mapped["Lead"] = relationship(back_populates="sticky_notes")


class LeadTask(Base):
    """A to-do attached to a lead — a comment, the action it calls for, and
    when it's due. Shown in the CRM's "Tâches" tab."""

    __tablename__ = "lead_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"))
    comment: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(300))
    due_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    # Once marked complete, the CRM locks the task's fields — matches
    # Salesforce's "Mark as Complete" pattern instead of a plain delete.
    completed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    lead: Mapped["Lead"] = relationship(back_populates="tasks")


class LeadDocument(Base):
    __tablename__ = "lead_documents"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), index=True)
    document_label: Mapped[str] = mapped_column(String(200))
    original_filename: Mapped[str] = mapped_column(String(300))
    stored_filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int] = mapped_column()
    file_url: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    lead: Mapped["Lead"] = relationship(back_populates="documents")


class LeadActivity(Base):
    """One entry in a lead's unified activity timeline — a status/field
    change, a note/task/document being added, a call being booked, etc.
    Replaces "reconstructing history by comparing timestamps across three
    separate tables" with a single, actor-attributed, chronological log.
    `actor_id` is NULL for actions with no logged-in user behind them (the
    public site creating a lead) — see routers/leads.py's _log_activity."""

    __tablename__ = "lead_activities"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    # For a field-change entry (action="field_changed"): which field, and
    # its before/after values, stringified — enough to render "Statut :
    # Nouveau → Contacté" without needing to know each field's real type.
    field: Mapped[str | None] = mapped_column(String(50), nullable=True)
    old_value: Mapped[str | None] = mapped_column(String(300), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # A ready-to-display sentence for actions that aren't a simple field
    # change (e.g. "a uploadé le document Carte grise").
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    lead: Mapped["Lead"] = relationship(back_populates="activity", foreign_keys=[lead_id])
    actor: Mapped["User | None"] = relationship(foreign_keys=[actor_id])


class GuideStatus(str, enum.Enum):
    brouillon = "Brouillon"
    publie = "Publié"


class Guide(Base):
    __tablename__ = "guides"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    slug: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(100))
    status: Mapped[GuideStatus] = mapped_column(SAEnum(GuideStatus), default=GuideStatus.brouillon)

    # Article hero fields
    category_href: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Short line shown under the title in the article hero.
    subtitle: Mapped[str | None] = mapped_column(Text, nullable=True)
    intro: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    author_avatar: Mapped[str | None] = mapped_column(String(300), nullable=True)
    editor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_date: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reading_time: Mapped[str | None] = mapped_column(String(50), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Content blocks — stored as JSON array
    blocks: Mapped[Any] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class Author(Base):
    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class Questionnaire(Base):
    __tablename__ = "questionnaires"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    questions: Mapped[list["Question"]] = relationship(
        back_populates="questionnaire", cascade="all, delete-orphan", order_by="Question.order"
    )


class Question(Base):
    """A question included in a questionnaire, referencing a fixed entry in
    app/question_catalog.py by key. The catalog defines the question's type
    and options; the CRM can only include/exclude/reorder questions and
    override their wording here (see the *_override columns)."""

    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    questionnaire_id: Mapped[int] = mapped_column(ForeignKey("questionnaires.id"))
    catalog_key: Mapped[str] = mapped_column(String(120))
    order: Mapped[int] = mapped_column(default=0)
    question_override: Mapped[str | None] = mapped_column(String(500), nullable=True)
    hint_override: Mapped[str | None] = mapped_column(String(500), nullable=True)
    placeholder_override: Mapped[str | None] = mapped_column(String(300), nullable=True)

    questionnaire: Mapped["Questionnaire"] = relationship(back_populates="questions")


class ConsultantBooking(Base):
    """One consultant's claim on a date/time slot. A "consultant" is just a
    User with role=consultant — there used to be a separate `Consultant`
    table loosely linked to a login via an optional FK, which meant a
    consultant could exist as two different, only-sometimes-connected
    accounts; `consultant_id` now points straight at users.id, so
    "consultant" is only ever a role, never a second identity.
    `lead_id` goes NULL if the lead is later hard-deleted (same pattern as
    LeadContact) — the booking itself, and the consultant's calendar, still
    stand."""

    __tablename__ = "consultant_bookings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    consultant_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    time: Mapped[str] = mapped_column(String(5))   # HH:MM
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    consultant: Mapped["User"] = relationship()


class ConsultantUnavailability(Base):
    """A timeframe a user (consultant or otherwise — anyone can track their
    own time off, but only a role=consultant, active User is ever offered
    to a prospect, see get_availability) has deliberately blocked off —
    e.g. a sick day — distinct from ConsultantBooking, which only records
    slots a client has actually booked. `time` NULL means the whole day is
    blocked; a specific "HH:MM" blocks just that one slot."""

    __tablename__ = "consultant_unavailabilities"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    consultant_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    time: Mapped[str | None] = mapped_column(String(5), nullable=True)  # HH:MM, or NULL for the whole day
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    consultant: Mapped["User"] = relationship()


class User(Base):
    """A CRM staff account. Created via the create_user management script
    or, once at least one superadmin exists, the CRM's own Users page
    (superadmin-only) — see app/auth.py for password hashing and the JWT
    issued on login."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    # The login identifier (not email — see main.py's migration, which
    # backfills this for accounts created before the switch).
    username: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.consultant)
    active: Mapped[bool] = mapped_column(default=True)
    # Bumped whenever every session should be killed *right now* — force
    # logout, a password change, refresh-token reuse-detection (see
    # app/auth.py's revoke_all_refresh_tokens). Revoking the refresh tokens
    # alone still leaves an already-issued access token usable for up to
    # ACCESS_TOKEN_TTL (30 min), since it's a self-contained JWT nothing
    # checks against the DB per request — get_current_user compares this
    # against the token's own "iat" so a token issued before this moment
    # stops working immediately, not just once it naturally expires.
    sessions_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class RefreshToken(Base):
    """A long-lived, revocable credential issued alongside a short-lived
    JWT access token (see app/auth.py) — the access token alone can't be
    revoked before it expires, which is what this exists to fix. Only the
    SHA-256 hash of the actual token is stored, same reasoning as
    password_hash: a DB read alone should never hand out something usable
    for login. `rotated_to_id` chains a token to whatever replaced it, so
    reusing an already-rotated token (a stolen-token symptom) can be
    detected and answered by revoking the whole chain."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)

    user: Mapped["User"] = relationship()


class RolePermission(Base):
    """Whether a given (non-superadmin) role may perform one action on one
    resource — the matrix a superadmin edits from the CRM's Permissions
    page. superadmin itself is never represented here: it's hardcoded to
    always pass in app/auth.py's require_permission, so it can't be
    misconfigured into locking itself out."""

    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role", "resource", "action", name="uq_role_permission"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), index=True)
    resource: Mapped[PermissionResource] = mapped_column(SAEnum(PermissionResource))
    action: Mapped[PermissionAction] = mapped_column(SAEnum(PermissionAction))
    allowed: Mapped[bool] = mapped_column(default=False)


class Notification(Base):
    """A per-user notification, created by some backend action (currently
    just lead assignment — see app/routers/leads.py) and read by the CRM's
    notification bell, which polls for unread ones rather than anything
    push-based."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(String(500))
    # Where clicking the notification should take you, e.g. "/dashboard/leads/42".
    link: Mapped[str | None] = mapped_column(String(300), nullable=True)
    read: Mapped[bool] = mapped_column(default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class AccountType(str, enum.Enum):
    """The public site's own customer-facing accounts — entirely separate
    from User (CRM staff). "client" gets their own devis/leads status,
    "partenaire" gets a referral code/link."""
    client = "client"
    partenaire = "partenaire"


class OAuthProvider(str, enum.Enum):
    google = "google"
    apple = "apple"
    facebook = "facebook"


class Account(Base):
    """A public-site customer or partner account. password_hash is nullable
    because an OAuth-only account (signed up via Google/Apple/Facebook)
    never sets one — see app/account_auth.py."""

    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("oauth_provider", "oauth_subject", name="uq_account_oauth_identity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    type: Mapped[AccountType] = mapped_column(SAEnum(AccountType))
    # Only ever set for type=partenaire — their shareable referral code.
    referral_code: Mapped[str | None] = mapped_column(String(20), unique=True, index=True, nullable=True)
    oauth_provider: Mapped[OAuthProvider | None] = mapped_column(SAEnum(OAuthProvider), nullable=True)
    oauth_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(default=True)
    # Same immediate-revocation mechanism as User.sessions_revoked_at (see
    # app/account_auth.py) — bumped on password change / "log out everywhere".
    sessions_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class AccountRefreshToken(Base):
    """Mirrors RefreshToken, scoped to Account instead of User — kept as a
    separate table (rather than reusing RefreshToken) so a leaked customer
    session can never be confused with, or accidentally granted, staff
    access."""

    __tablename__ = "account_refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)

    account: Mapped["Account"] = relationship()


class AccountPasswordResetToken(Base):
    """A single-use, short-lived token emailed to an account for the
    "mot de passe oublié" flow — only the hash is stored, same reasoning as
    password_hash/RefreshToken.token_hash."""

    __tablename__ = "account_password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    account: Mapped["Account"] = relationship()
