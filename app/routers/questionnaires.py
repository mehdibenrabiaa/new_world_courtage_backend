from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.auth import require_permission
from app.database import get_db
from app.models import Questionnaire, Question
from app.question_catalog import get_catalog, get_catalog_entry
from app.schemas import (
    QuestionnaireCreate, QuestionnaireUpdate, QuestionnaireOut,
    CatalogEntryOut, QuestionAdd, QuestionWordingUpdate, QuestionOut, RuleOut,
)

router = APIRouter(prefix="/questionnaires", tags=["questionnaires"])

view_questionnaires = require_permission("questionnaires", "view")
create_questionnaires = require_permission("questionnaires", "create")
edit_questionnaires = require_permission("questionnaires", "edit")
delete_questionnaires = require_permission("questionnaires", "delete")


def _get_questionnaire_or_404(slug: str, db: Session) -> Questionnaire:
    questionnaire = db.query(Questionnaire).filter(Questionnaire.slug == slug).first()
    if not questionnaire:
        raise HTTPException(status_code=404, detail="Questionnaire introuvable.")
    return questionnaire


def _get_question_or_404(question_id: int, db: Session) -> Question:
    question = db.get(Question, question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question introuvable.")
    return question


def _merge(question: Question, template: str, catalog_key_to_id: dict[str, int]) -> QuestionOut:
    """Combine a Question row with its catalog entry (by template + catalog_key)
    into the shape both the CRM and the public site consume."""
    entry = get_catalog_entry(template, question.catalog_key)
    if entry is None:
        return QuestionOut(
            id=question.id, questionnaire_id=question.questionnaire_id,
            catalog_key=question.catalog_key, key=question.catalog_key,
            section=None, eyebrow=None, type="input", input_type=None,
            question=question.question_override or f"[Question introuvable : {question.catalog_key}]",
            hint=question.hint_override, placeholder=question.placeholder_override,
            required=True, card=False, order=question.order, options=[], orphaned=True,
        )
    rules = []
    skip_unless = entry.get("skip_unless")
    if skip_unless:
        source_id = catalog_key_to_id.get(skip_unless["key"])
        if source_id is not None:
            rules.append(RuleOut(source_question_id=source_id, operator="not_equals", value=skip_unless["value"]))
    return QuestionOut(
        id=question.id,
        questionnaire_id=question.questionnaire_id,
        catalog_key=question.catalog_key,
        key=question.catalog_key,
        section=entry.get("section"),
        eyebrow=entry.get("eyebrow"),
        type=entry["type"],
        input_type=entry.get("input_type"),
        unit=entry.get("unit"),
        question=question.question_override or entry["question"],
        hint=question.hint_override if question.hint_override is not None else entry.get("hint"),
        placeholder=question.placeholder_override if question.placeholder_override is not None else entry.get("placeholder"),
        required=entry.get("required", True),
        card=entry.get("card", False),
        gate=entry.get("gate", False),
        products=entry.get("products"),
        order=question.order,
        options=entry.get("options", []),
        rules=rules,
    )


def _questionnaire_out(questionnaire: Questionnaire) -> QuestionnaireOut:
    questions = sorted(questionnaire.questions, key=lambda q: q.order)
    catalog_key_to_id = {q.catalog_key: q.id for q in questions}
    return QuestionnaireOut(
        id=questionnaire.id, slug=questionnaire.slug, name=questionnaire.name,
        questions=[_merge(q, questionnaire.slug, catalog_key_to_id) for q in questions],
    )


@router.get("", response_model=list[QuestionnaireOut])
def list_questionnaires(db: Session = Depends(get_db), _user=Depends(view_questionnaires)):
    questionnaires = db.query(Questionnaire).order_by(Questionnaire.name.asc()).all()
    return [_questionnaire_out(q) for q in questionnaires]


@router.post("", response_model=QuestionnaireOut, status_code=201)
def create_questionnaire(payload: QuestionnaireCreate, db: Session = Depends(get_db), _user=Depends(create_questionnaires)):
    existing = db.query(Questionnaire).filter(Questionnaire.slug == payload.slug).first()
    if existing:
        raise HTTPException(status_code=409, detail="Un questionnaire avec ce slug existe déjà.")
    questionnaire = Questionnaire(slug=payload.slug, name=payload.name)
    db.add(questionnaire)
    db.commit()
    db.refresh(questionnaire)
    return _questionnaire_out(questionnaire)


@router.get("/{slug}", response_model=QuestionnaireOut)
def get_questionnaire(slug: str, db: Session = Depends(get_db), _user=Depends(view_questionnaires)):
    return _questionnaire_out(_get_questionnaire_or_404(slug, db))


@router.patch("/{slug}", response_model=QuestionnaireOut)
def update_questionnaire(slug: str, payload: QuestionnaireUpdate, db: Session = Depends(get_db), _user=Depends(edit_questionnaires)):
    questionnaire = _get_questionnaire_or_404(slug, db)

    if payload.slug and payload.slug != questionnaire.slug:
        conflict = db.query(Questionnaire).filter(Questionnaire.slug == payload.slug).first()
        if conflict:
            raise HTTPException(status_code=409, detail="Un questionnaire avec ce slug existe déjà.")
        questionnaire.slug = payload.slug

    if payload.name:
        questionnaire.name = payload.name

    db.commit()
    db.refresh(questionnaire)
    return _questionnaire_out(questionnaire)


@router.get("/{slug}/catalog", response_model=list[CatalogEntryOut])
def list_available_catalog_entries(slug: str, db: Session = Depends(get_db), _user=Depends(view_questionnaires)):
    """Catalog entries not yet included in this questionnaire — what the CRM's
    "add a question" picker offers."""
    questionnaire = _get_questionnaire_or_404(slug, db)
    used_keys = {q.catalog_key for q in questionnaire.questions}
    return [
        CatalogEntryOut(**entry) for entry in get_catalog(questionnaire.slug)
        if entry["key"] not in used_keys
    ]


@router.get("/{slug}/questions", response_model=list[QuestionOut])
def list_published_questions(slug: str, db: Session = Depends(get_db)):
    """Flat, ordered list of included questions — consumed by the public
    site's questionnaire flow."""
    questionnaire = _get_questionnaire_or_404(slug, db)
    questions = sorted(questionnaire.questions, key=lambda q: q.order)
    catalog_key_to_id = {q.catalog_key: q.id for q in questions}
    return [_merge(q, questionnaire.slug, catalog_key_to_id) for q in questions]


@router.post("/{slug}/questions", response_model=QuestionOut, status_code=201)
def add_question(slug: str, payload: QuestionAdd, db: Session = Depends(get_db), _user=Depends(edit_questionnaires)):
    questionnaire = _get_questionnaire_or_404(slug, db)

    entry = get_catalog_entry(questionnaire.slug, payload.catalog_key)
    if entry is None:
        raise HTTPException(status_code=404, detail="Question introuvable dans le catalogue.")

    already = any(q.catalog_key == payload.catalog_key for q in questionnaire.questions)
    if already:
        raise HTTPException(status_code=409, detail="Cette question est déjà présente dans ce questionnaire.")

    question = Question(questionnaire_id=questionnaire.id, catalog_key=payload.catalog_key, order=payload.order)
    db.add(question)
    db.commit()
    db.refresh(question)
    catalog_key_to_id = {q.catalog_key: q.id for q in questionnaire.questions}
    return _merge(question, questionnaire.slug, catalog_key_to_id)


@router.patch("/questions/{question_id}", response_model=QuestionOut)
def update_question_wording(question_id: int, payload: QuestionWordingUpdate, db: Session = Depends(get_db), _user=Depends(edit_questionnaires)):
    question = _get_question_or_404(question_id, db)
    questionnaire = db.get(Questionnaire, question.questionnaire_id)

    if payload.question is not None:
        question.question_override = payload.question or None
    if payload.hint is not None:
        question.hint_override = payload.hint or None
    if payload.placeholder is not None:
        question.placeholder_override = payload.placeholder or None
    if payload.order is not None:
        question.order = payload.order

    db.commit()
    db.refresh(question)
    catalog_key_to_id = {q.catalog_key: q.id for q in questionnaire.questions}
    return _merge(question, questionnaire.slug, catalog_key_to_id)


@router.delete("/questions/{question_id}", status_code=204)
def remove_question(question_id: int, db: Session = Depends(get_db), _user=Depends(delete_questionnaires)):
    question = _get_question_or_404(question_id, db)
    db.delete(question)
    db.commit()
