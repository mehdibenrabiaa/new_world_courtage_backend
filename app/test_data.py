"""Generates a realistic fake "Assurance Garage" lead (identity fields +
answers) for exercising the CRM UI with data shaped exactly like a real
submission — same catalog keys/questions, same comma-joined option labels,
same JSON-stringified repeating-table rows (see garagiste/devis's own
handleSubmit, which this mirrors) — instead of hand-typing test leads one
field at a time in the CRM's own "Nouveau lead" dialog.

Only "garage" is modeled since it's the only questionnaire type the CRM has
a dedicated field set for today (see the CRM's own lead.type dispatch).
"""
import json
import random
from datetime import datetime, timedelta

from app.question_catalog import get_catalog

FIRST_NAMES = ["Karim", "Yasmine", "Mehdi", "Sofia", "Omar", "Nadia", "Rachid", "Amina", "Sami", "Leila", "Hakim", "Lina"]
LAST_NAMES = ["Benali", "Haddad", "El Amrani", "Ziani", "Bekkar", "Cherif", "Meziane", "Toumi", "Saadi", "Lahlou"]
CITIES = ["Paris", "Lyon", "Marseille", "Toulouse", "Nantes", "Bordeaux", "Lille", "Strasbourg", "Rennes", "Nice"]
COMPANIES = ["Axa", "Allianz", "MAIF", "MMA", "Groupama", "Generali", "AIG", "Aviva"]
SINISTRE_TYPES = ["Vol", "Incendie", "Dégât des eaux", "Bris de glace", "Collision", "Vandalisme"]
TEXT_SNIPPETS = ["Aucune remarque particulière.", "Dossier à traiter rapidement.", "Voir pièces jointes.", "RAS."]

# The two identity keys that are ALSO real "Coordonnées"/"Risques" catalog
# entries but end up on dedicated Lead columns instead of a LeadAnswer row
# (see garagiste/devis's IDENTITY_KEYS) — excluded from the generated
# answers list the same way.
IDENTITY_KEYS = {"representant_legal", "mobile", "email_principal", "siret", "activite_principale"}

# Every repeating-table question in the garage catalog — type "input" like
# any text field, but the public form renders/serializes it as a table (see
# CarInsuranceForm.js's REPEATING_TABLE_FIELDS). Detected here by key rather
# than any catalog flag, same as the CRM's own looksLikeRepeatingRows()
# detects it from the stored *shape* on the read side.
REPEATING_TABLE_KEYS = {
    "garage_flotte_vehicules_table", "garage_plaques_w_table", "negociant_historique_contrats_table",
    "convoyeur_historique_contrats_table", "garage_historique_contrats", "convoyeur_conducteurs_table",
    "pct_detention_capital", "garage_sinistres_hors_auto_table", "garage_sinistres_auto_table",
}

ALL_PRODUCTS = ["protect_garage", "convoyeurs", "negociants"]


def _fake_iso_date(years_ago: int = 3) -> str:
    """YYYY-MM-DD — the shape a plain <input type="date"> answer is stored
    in (see the CRM's own formatDateValue)."""
    days = random.randint(0, 365 * years_ago)
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def _fake_fr_date(years_ago: int = 3) -> str:
    """DD/MM/YYYY — repeating-table row dates are already French-formatted
    at submit time (see garagiste/devis's toFrenchDate), unlike plain date
    answers above."""
    days = random.randint(0, 365 * years_ago)
    return (datetime.now() - timedelta(days=days)).strftime("%d/%m/%Y")


def _fake_phone() -> str:
    return "06" + "".join(str(random.randint(0, 9)) for _ in range(8))


def _fake_siret() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(14))


def _fake_plate() -> str:
    letters = "ABCDEFGHJKLMNPQRSTVWXYZ"
    return f"{random.choice(letters)}{random.choice(letters)}-{random.randint(100,999)}-{random.choice(letters)}{random.choice(letters)}"


def _repeating_rows(key: str) -> list[dict]:
    n = random.randint(1, 3)
    if key == "garage_flotte_vehicules_table":
        rows = [[
            ("Immatriculation", _fake_plate()),
            ("Usage", random.choice(["Courtoisie", "Location", "Société", "Gérant"])),
            ("Mode d'achat", random.choice(["Comptant", "Crédit-LOA", "LLD", "Autre"])),
        ] for _ in range(n)]
    elif key == "garage_plaques_w_table":
        rows = [[
            ("N° de plaque W", f"W{random.randint(100, 999)}"),
            ("Date de délivrance", _fake_fr_date()),
            ("Date de sortie", _fake_fr_date()),
        ] for _ in range(n)]
    elif key in ("negociant_historique_contrats_table", "convoyeur_historique_contrats_table", "garage_historique_contrats"):
        rows = [[
            ("Compagnie", random.choice(COMPANIES)),
            ("Date début", _fake_fr_date()),
            ("Date d'échéance", _fake_fr_date()),
        ] for _ in range(n)]
    elif key == "convoyeur_conducteurs_table":
        rows = [[
            ("Nom", random.choice(LAST_NAMES)),
            ("Prénom", random.choice(FIRST_NAMES)),
            ("Date de naissance", _fake_fr_date(years_ago=40)),
        ] for _ in range(n)]
    elif key == "pct_detention_capital":
        rows = [[
            ("Nom complet", f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"),
            ("% détention du capital", f"{random.randint(5, 100)}%"),
            ("Civilité", random.choice(["M.", "Mme"])),
            ("Date de naissance", _fake_fr_date(years_ago=40)),
            ("Commune de naissance", random.choice(CITIES)),
        ] for _ in range(n)]
    elif key == "garage_sinistres_hors_auto_table":
        rows = [[
            ("Date de survenance", _fake_fr_date()),
            ("Type", random.choice(SINISTRE_TYPES)),
            ("Montant (€)", str(random.randint(200, 20000))),
            ("Sinistre clos ?", random.choice(["Oui", "Non"])),
        ] for _ in range(n)]
    elif key == "garage_sinistres_auto_table":
        rows = [[
            ("Date de survenance", _fake_fr_date()),
            ("Type", random.choice(SINISTRE_TYPES)),
            ("Nature", random.choice(["Matériel", "Corporel"])),
            ("Resp. (%)", f"{random.choice([0, 25, 50, 75, 100])}%"),
            ("Montant (€)", str(random.randint(200, 20000))),
        ] for _ in range(n)]
    else:
        rows = []
    return [{"fields": [{"label": label, "value": value} for label, value in row]} for row in rows]


def _scalar_value(entry: dict) -> tuple[str, str | None]:
    """Returns (value_to_store, raw_value_for_skip_unless_tracking) for a
    non-repeating-table, non-radio/checkbox question."""
    input_type = entry.get("input_type")
    if input_type == "date":
        return _fake_iso_date(), None
    if input_type == "month":
        return _fake_iso_date()[:7], None
    if input_type == "tel":
        return _fake_phone(), None
    if input_type == "email":
        first, last = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
        return f"{first.lower()}.{last.lower()}@example.com", None
    if input_type == "number":
        unit = entry.get("unit")
        if unit == "percent":
            return str(random.randint(5, 100)), None
        if unit == "eur":
            return str(random.randint(1000, 500000)), None
        if unit == "m2":
            return str(random.randint(20, 800)), None
        return str(random.randint(1, 50)), None
    return random.choice(TEXT_SNIPPETS), None


def generate_fake_garage_lead() -> dict:
    """Builds one fake garage lead's full data: identity fields (for the
    Lead's own columns) + a list of {catalog_key, question, value} answers,
    walking the real catalog in order so `skip_unless`/`products` gating
    behaves exactly like a real submission (a conditional field only gets a
    fake answer if its trigger question was fake-answered the matching way,
    and a product-tagged field only appears for a lead that "picked" that
    product)."""
    selected_products = random.sample(ALL_PRODUCTS, random.choice([1, 1, 1, 2]))
    catalog = get_catalog("garage")

    raw_values: dict[str, str] = {}
    answers: list[dict] = []
    activite_labels: list[str] = []

    for entry in catalog:
        key = entry["key"]
        if entry.get("gate"):
            # produits_interesses itself — its own catalog entry — is a real
            # answer row on a genuine submission too (only the 5 IDENTITY_KEYS
            # below are excluded), so it's generated the same way here.
            labels = [o["label"] for o in entry["options"] if o["value"] in selected_products]
            raw_values[key] = ",".join(selected_products)
            answers.append({"catalog_key": key, "question": entry["question"], "value": ", ".join(labels)})
            continue

        if key == "activite_principale":
            # Feeds Lead.activite (raw, comma-joined VALUES — matches the
            # public site's own quirk of not label-mapping this one field,
            # see garagiste/devis's handleSubmit) instead of a LeadAnswer row.
            if "protect_garage" in selected_products:
                chosen = random.sample(entry["options"], random.randint(1, 2))
                activite_labels = [o["value"] for o in chosen]
            continue
        if key in IDENTITY_KEYS:
            continue

        products = entry.get("products")
        if products and not any(p in selected_products for p in products):
            continue

        skip_unless = entry.get("skip_unless")
        if skip_unless and raw_values.get(skip_unless["key"]) != skip_unless["value"]:
            continue

        # Optional fields are sometimes left blank too, so the CRM's "no
        # options to enumerate" / empty-value fallbacks get exercised.
        if entry.get("required") is False and random.random() < 0.3:
            continue

        if key in REPEATING_TABLE_KEYS:
            answers.append({"catalog_key": key, "question": entry["question"], "value": json.dumps(_repeating_rows(key))})
            continue

        qtype = entry["type"]
        if qtype in ("radio", "checkbox"):
            options = entry["options"]
            if qtype == "radio":
                chosen = [random.choice(options)]
            else:
                chosen = random.sample(options, random.randint(1, min(2, len(options))))
            raw_values[key] = ",".join(o["value"] for o in chosen)
            answers.append({"catalog_key": key, "question": entry["question"], "value": ", ".join(o["label"] for o in chosen)})
            continue

        value, raw = _scalar_value(entry)
        if raw is not None:
            raw_values[key] = raw
        answers.append({"catalog_key": key, "question": entry["question"], "value": value})

    first, last = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
    return {
        "name": f"{first} {last}",
        "phone": _fake_phone(),
        "email": f"{first.lower()}.{last.lower()}@example.com",
        "siret": _fake_siret(),
        "activite": ",".join(activite_labels) or None,
        "answers": answers,
    }
