from datetime import datetime, timezone
from typing import Any
from sqlalchemy import String, Text, DateTime, JSON, Enum as SAEnum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
import enum


class LeadStatus(str, enum.Enum):
    new = "new"
    contacted = "contacted"
    qualified = "qualified"
    converted = "converted"
    lost = "lost"


class LeadType(str, enum.Enum):
    """A lead's category — matches the guide categories used across the site
    (see the CRM's lib/categories.ts, which is the source of truth for the
    exact wording)."""
    flotte_transport = "Assurance Flotte & Transport"
    taxi = "Assurance Taxi"
    ambulance = "Assurance Ambulance"
    vtc = "Assurance VTC"
    pro_auto = "Assurance Pro de l'auto"
    garage = "Assurance Garage"
    construction = "Assurance Construction"
    immobilier = "Assurance Immobilier"
    general = "Assurance Général"


class Lead(Base):
    __tablename__ = "leads"

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
    # "Deleting" a lead from the CRM only hides it (excluded from list_leads) —
    # it stays in the DB so nothing is ever lost to an accidental click.
    deleted: Mapped[bool] = mapped_column(default=False)
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
