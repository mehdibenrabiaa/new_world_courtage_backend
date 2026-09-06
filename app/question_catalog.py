"""Hardcoded question catalogs — one list per questionnaire slug.

Each entry defines a question's *identity* (key, type, options) — none of
that is editable from the CRM. The CRM can only:
  - include/exclude an entry in a given questionnaire (add/remove), and
  - override its wording (question text, hint, placeholder) and its order.

Content here mirrors the vehicle/coverage questions already authored in the
site's own components/CarInsuranceForm.js DEFAULT_STEPS, minus whatever
VehicleIdentityForm already collects upstream (name, phone, permis date,
bonus-malus prefill, naissance, immat) so nothing is asked twice.
"""

CATALOGS: dict[str, list[dict]] = {
    "taxi": [
        {"key": "vehicle_choice", "section": "Véhicule", "type": "radio", "card": True,
         "eyebrow": "Pour commencer", "question": "Quel véhicule souhaitez-vous assurer ?",
         "options": [{"label": "Mon véhicule actuel", "value": "current"}, {"label": "Un futur achat", "value": "future"}]},
        {"key": "vehicle_type", "section": "Véhicule", "type": "radio", "card": True,
         "question": "Quel type de véhicule souhaitez-vous assurer ?",
         "options": [{"label": "Moto", "value": "moto"}, {"label": "Scooter", "value": "scooter"}, {"label": "Voiture", "value": "voiture"}]},
        {"key": "brand", "section": "Véhicule", "type": "input", "input_type": "text",
         "question": "Marque du véhicule", "placeholder": "Ex : Renault, Yamaha…"},
        {"key": "model", "section": "Véhicule", "type": "input", "input_type": "text",
         "question": "Modèle", "placeholder": "Ex : Clio, Série 3, CB500…"},
        {"key": "version", "section": "Véhicule", "type": "input", "input_type": "text",
         "question": "Version ou finition", "placeholder": "Ex : Sport, Confort, 1.5 dCi…"},
        {"key": "displacement", "section": "Véhicule", "type": "select", "question": "Cylindrée",
         "options": [
             {"label": "Moins de 50 cc", "value": "<50"}, {"label": "50 – 125 cc", "value": "50-125"},
             {"label": "126 – 250 cc", "value": "126-250"}, {"label": "251 – 500 cc", "value": "251-500"},
             {"label": "501 – 750 cc", "value": "501-750"}, {"label": "Plus de 750 cc", "value": ">750"},
         ]},
        {"key": "power", "section": "Véhicule", "type": "input", "input_type": "number",
         "question": "Puissance du moteur", "placeholder": "Puissance en kW ou CV"},
        {"key": "purchase_date", "section": "Véhicule", "type": "input", "input_type": "month",
         "question": "Date d'achat du véhicule"},
        {"key": "purchase_price", "section": "Véhicule", "type": "input", "input_type": "number",
         "question": "Prix d'achat", "placeholder": "Montant en €"},
        {"key": "first_registration", "section": "Véhicule", "type": "input", "input_type": "month",
         "question": "Première mise en circulation"},
        {"key": "condition", "section": "Véhicule", "type": "radio", "question": "Neuf ou d'occasion ?",
         "options": [{"label": "Neuf", "value": "neuf"}, {"label": "Occasion", "value": "occasion"}]},
        {"key": "usage", "section": "Véhicule", "type": "radio", "question": "Usage principal du véhicule",
         "options": [
             {"label": "Domicile – travail", "value": "commute"}, {"label": "Loisirs", "value": "leisure"},
             {"label": "Professionnel", "value": "professional"},
         ]},
        {"key": "mileage", "section": "Véhicule", "type": "select", "question": "Kilométrage annuel estimé",
         "options": [
             {"label": "Moins de 5 000 km", "value": "<5000"}, {"label": "5 000 – 10 000 km", "value": "5000-10000"},
             {"label": "10 000 – 15 000 km", "value": "10000-15000"}, {"label": "15 000 – 20 000 km", "value": "15000-20000"},
             {"label": "Plus de 20 000 km", "value": ">20000"},
         ]},
        {"key": "parking", "section": "Véhicule", "type": "radio", "question": "Stationnement la nuit",
         "options": [
             {"label": "Garage privé", "value": "garage"}, {"label": "Rue", "value": "street"},
             {"label": "Parking privé", "value": "private_parking"},
         ]},
        {"key": "antitheft", "section": "Véhicule", "type": "checkbox", "required": False,
         "hint": "Sélectionnez tout ce qui s'applique.", "question": "Dispositifs antivol",
         "options": [
             {"label": "Alarme", "value": "alarm"}, {"label": "Antivol mécanique", "value": "lock"},
             {"label": "Traceur GPS", "value": "tracker"},
         ]},
        {"key": "financing", "section": "Véhicule", "type": "radio", "question": "Financement du véhicule",
         "options": [
             {"label": "Comptant", "value": "owned"}, {"label": "Crédit", "value": "loan"},
             {"label": "Leasing", "value": "leasing"},
         ]},
        {"key": "age", "section": "Conducteur", "type": "select", "question": "Votre âge",
         "options": [
             {"label": "16 – 24 ans", "value": "16-24"}, {"label": "25 – 35 ans", "value": "25-35"},
             {"label": "36 – 45 ans", "value": "36-45"}, {"label": "46 – 55 ans", "value": "46-55"},
             {"label": "56 – 65 ans", "value": "56-65"}, {"label": "66 ans et plus", "value": "66+"},
         ]},
        {"key": "license_type", "section": "Conducteur", "type": "select", "question": "Type de permis",
         "options": [
             {"label": "Permis A – moto (> 35 kW)", "value": "A"}, {"label": "Permis A2 – moto limitée", "value": "A2"},
             {"label": "Permis A1 – 125 cc", "value": "A1"}, {"label": "Permis B – voiture", "value": "B"},
             {"label": "Permis AM – cyclomoteur", "value": "AM"},
         ]},
        {"key": "experience", "section": "Conducteur", "type": "select", "question": "Années d'expérience",
         "options": [
             {"label": "Moins d'1 an", "value": "<1"}, {"label": "1 – 3 ans", "value": "1-3"},
             {"label": "4 – 6 ans", "value": "4-6"}, {"label": "7 – 10 ans", "value": "7-10"},
             {"label": "11 – 20 ans", "value": "11-20"}, {"label": "Plus de 20 ans", "value": "20+"},
         ]},
        {"key": "primary_driver", "section": "Conducteur", "type": "radio", "question": "Êtes-vous le conducteur principal ?",
         "options": [{"label": "Conducteur principal", "value": "main"}, {"label": "Conducteur secondaire", "value": "secondary"}]},
        {"key": "additional_drivers", "section": "Conducteur", "type": "select", "question": "Conducteurs supplémentaires",
         "options": [
             {"label": "Aucun", "value": "0"}, {"label": "1 conducteur", "value": "1"},
             {"label": "2 conducteurs", "value": "2"}, {"label": "3 ou plus", "value": "3+"},
         ]},
        {"key": "claims_declared", "section": "Historique", "type": "radio",
         "question": "Sinistres déclarés ces 3 à 5 dernières années ?",
         "options": [{"label": "Oui", "value": "oui"}, {"label": "Non", "value": "non"}]},
        {"key": "claims_responsibility", "section": "Historique", "type": "radio",
         "question": "Ces sinistres étaient-ils de votre responsabilité ?",
         "options": [
             {"label": "Responsable", "value": "responsible"}, {"label": "Non responsable", "value": "not_responsible"},
             {"label": "Les deux", "value": "both"},
         ]},
        {"key": "bonus_malus", "section": "Historique", "type": "select", "question": "Coefficient bonus-malus",
         "options": [
             {"label": "0.50 — Bonus maximum", "value": "0.50"}, {"label": "0.51 – 0.79 — Bon conducteur", "value": "0.51-0.79"},
             {"label": "0.80 – 0.99 — Conducteur confirmé", "value": "0.80-0.99"}, {"label": "1.00 — Référence", "value": "1.00"},
             {"label": "1.01 – 1.25 — Malus léger", "value": "1.01-1.25"}, {"label": "1.26 – 2.00 — Malus modéré", "value": "1.26-2.00"},
             {"label": "2.01 – 3.50 — Malus élevé", "value": "2.01-3.50"},
         ]},
        {"key": "license_suspended", "section": "Historique", "type": "radio",
         "question": "Votre permis a-t-il déjà été suspendu ou annulé ?",
         "options": [{"label": "Oui", "value": "oui"}, {"label": "Non", "value": "non"}]},
        {"key": "coverage_type", "section": "Couverture", "type": "radio", "question": "Type de couverture souhaité",
         "options": [{"label": "Au tiers", "value": "third-party"}, {"label": "Tous risques", "value": "comprehensive"}]},
        {"key": "deductible", "section": "Couverture", "type": "select", "question": "Montant de franchise",
         "options": [
             {"label": "150 €", "value": "150"}, {"label": "300 €", "value": "300"}, {"label": "500 €", "value": "500"},
             {"label": "750 €", "value": "750"}, {"label": "1 000 €", "value": "1000"},
         ]},
        {"key": "legal_protection", "section": "Couverture", "type": "radio", "question": "Protection juridique ?",
         "options": [{"label": "Oui", "value": "oui"}, {"label": "Non", "value": "non"}]},
        {"key": "roadside_assistance", "section": "Couverture", "type": "radio", "question": "Assistance routière ?",
         "options": [{"label": "Oui", "value": "oui"}, {"label": "Non", "value": "non"}]},
        {"key": "replacement_vehicle", "section": "Couverture", "type": "radio",
         "question": "Véhicule de remplacement en cas de sinistre ?",
         "options": [{"label": "Oui", "value": "oui"}, {"label": "Non", "value": "non"}]},
        {"key": "effective_date", "section": "Contrat", "type": "input", "input_type": "date",
         "question": "Date de prise d'effet"},
        {"key": "payment_frequency", "section": "Contrat", "type": "radio", "question": "Fréquence de paiement",
         "options": [{"label": "Mensuelle", "value": "monthly"}, {"label": "Annuelle", "value": "yearly"}]},
        {"key": "preferences", "section": "Contrat", "type": "input", "input_type": "text", "required": False,
         "question": "Préférences ou contraintes", "placeholder": "Ex : assureur actuel, exclusions…"},
        {"key": "email", "section": "Contact", "type": "input", "input_type": "email",
         "question": "Adresse e-mail", "placeholder": "exemple@email.com"},
    ],
    # Transcribed from Formulaire_Garage_PROTECT_Remplissable.pdf, regrouped
    # into the 5 sections the site's step tabs should show (Coordonnées,
    # Risques, Antécédents, Flotte auto propre, Tarification) rather than the
    # PDF's own §-numbered groupings. That PDF's section numbering jumps from
    # 4 to 8 (no 5/6/7 in the pages provided) — this catalog only covers what
    # was in those pages, plus a placeholder Tarification pair (effective
    # date / payment frequency, mirroring taxi's contract questions) since
    # the PDF didn't include a pricing section at all — replace those two
    # once the real tarification fields are available. The vehicle-fleet
    # table (§10) is a repeating grid in the PDF but this catalog has no
    # repeating-row concept, so it's flattened into summary questions.
    "garage": [
        # — Coordonnées —
        {"key": "representant_legal", "section": "Coordonnées", "type": "input", "input_type": "text",
         "question": "Nom et prénom du représentant légal"},
        {"key": "mobile", "section": "Coordonnées", "type": "input", "input_type": "tel",
         "question": "Mobile"},
        {"key": "email_principal", "section": "Coordonnées", "type": "input", "input_type": "email",
         "question": "Email principal"},
        {"key": "siret", "section": "Coordonnées", "type": "input", "input_type": "text",
         "question": "SIRET"},
        {"key": "raison_sociale", "section": "Coordonnées", "type": "input", "input_type": "text",
         "question": "Raison sociale"},
        {"key": "code_ape", "section": "Coordonnées", "type": "input", "input_type": "text", "required": False,
         "question": "Code APE"},
        {"key": "adresse_siege_social", "section": "Coordonnées", "type": "input", "input_type": "text",
         "question": "Adresse siège social"},
        {"key": "date_naissance", "section": "Coordonnées", "type": "input", "input_type": "date",
         "question": "Date de naissance"},
        {"key": "commune_naissance", "section": "Coordonnées", "type": "input", "input_type": "text",
         "question": "Commune de naissance"},
        {"key": "pct_detention_capital", "section": "Coordonnées", "type": "input", "input_type": "number",
         "question": "% détention du capital"},

        # — Risques —
        {"key": "activite_principale", "section": "Risques", "type": "radio",
         "question": "Activité principale",
         "options": [
             {"label": "Mécanicien réparateur automobile", "value": "mecanicien"},
             {"label": "Carrossier / tôlier", "value": "carrossier"},
             {"label": "Centre d'entretien automobile", "value": "centre_entretien"},
         ]},
        {"key": "activites_annexes", "section": "Risques", "type": "checkbox", "required": False,
         "question": "Activités annexes",
         "options": [
             {"label": "Carrossier / tôlier", "value": "carrossier"},
             {"label": "Centre d'entretien automobile", "value": "centre_entretien"},
             {"label": "Vente de véhicules dans la limite de 20% du CA", "value": "vente_vehicules"},
             {"label": "Location de véhicules <= 3,5 t et 7 places maximum, dans la limite de 10% du CA", "value": "location_vehicules"},
         ]},
        {"key": "effectif_total", "section": "Risques", "type": "input", "input_type": "number",
         "question": "Effectif total hors personnel administratif"},
        {"key": "surface_risque", "section": "Risques", "type": "input", "input_type": "number",
         "question": "Surface du risque en m²"},

        # — Antécédents —
        {"key": "date_creation", "section": "Antécédents", "type": "input", "input_type": "date",
         "question": "Date de création"},
        {"key": "entreprise_deja_creee", "section": "Antécédents", "type": "radio",
         "question": "L'entreprise est-elle déjà créée ?",
         "options": [{"label": "Oui", "value": "oui"}, {"label": "Non", "value": "non"}]},
        {"key": "entreprise_resiliee", "section": "Antécédents", "type": "radio",
         "question": "L'entreprise a-t-elle été résiliée par son assureur ?",
         "options": [{"label": "Oui", "value": "oui"}, {"label": "Non", "value": "non"}]},
        {"key": "motif_resiliation", "section": "Antécédents", "type": "input", "input_type": "text", "required": False,
         "question": "Si oui, motif de résiliation"},
        {"key": "motif_resiliation_date", "section": "Antécédents", "type": "input", "input_type": "date", "required": False,
         "question": "Date de résiliation"},
        {"key": "deja_assuree_36_mois", "section": "Antécédents", "type": "radio",
         "question": "L'entreprise a-t-elle déjà été assurée sur les 36 derniers mois ?",
         "options": [{"label": "Oui", "value": "oui"}, {"label": "Non", "value": "non"}]},
        {"key": "compagnie_precedente", "section": "Antécédents", "type": "input", "input_type": "text", "required": False,
         "question": "Compagnie"},
        {"key": "assurance_depuis", "section": "Antécédents", "type": "input", "input_type": "date", "required": False,
         "question": "Depuis"},
        {"key": "assurance_date_resiliation", "section": "Antécédents", "type": "input", "input_type": "date", "required": False,
         "question": "Date résiliation"},
        {"key": "assurance_date_echeance", "section": "Antécédents", "type": "input", "input_type": "date", "required": False,
         "question": "Date d'échéance"},
        {"key": "sinistres_hors_auto", "section": "Antécédents", "type": "radio",
         "question": "Y a-t-il eu des sinistres hors-auto sur les 36 derniers mois ?",
         "hint": "La sinistralité doit correspondre à celle indiquée dans votre relevé de sinistres.",
         "options": [{"label": "Oui", "value": "oui"}, {"label": "Non", "value": "non"}]},
        {"key": "sinistres_hors_auto_nombre", "section": "Antécédents", "type": "input", "input_type": "number", "required": False,
         "question": "Nombre de sinistres (hors-auto)"},
        {"key": "sinistres_hors_auto_montant", "section": "Antécédents", "type": "input", "input_type": "number", "required": False,
         "question": "Montant / charge (hors-auto)"},
        {"key": "sinistres_auto", "section": "Antécédents", "type": "radio",
         "question": "Y a-t-il eu des sinistres auto (hors bris de glace et grêle) sur les 36 derniers mois ?",
         "hint": "La sinistralité doit correspondre à celle indiquée dans votre relevé de sinistres.",
         "options": [{"label": "Oui", "value": "oui"}, {"label": "Non", "value": "non"}]},
        {"key": "sinistres_auto_nombre", "section": "Antécédents", "type": "input", "input_type": "number", "required": False,
         "question": "Nombre de sinistres (auto)"},
        {"key": "sinistres_auto_montant", "section": "Antécédents", "type": "input", "input_type": "number", "required": False,
         "question": "Montant / charge (auto)"},

        # — Flotte auto propre —
        {"key": "w_garage", "section": "Flotte auto propre", "type": "input", "input_type": "text",
         "question": "W Garage"},
        {"key": "flotte_nombre_vehicules", "section": "Flotte auto propre", "type": "input", "input_type": "number",
         "question": "Nombre de véhicules dans la flotte"},
        {"key": "flotte_immatriculations", "section": "Flotte auto propre", "type": "input", "input_type": "text", "required": False,
         "question": "Immatriculations (carte grise) des véhicules",
         "placeholder": "Ex : AB-123-CD, EF-456-GH…"},

        # — Tarification (placeholder pair — PDF had no pricing section) —
        {"key": "effective_date", "section": "Tarification", "type": "input", "input_type": "date",
         "question": "Date de prise d'effet souhaitée"},
        {"key": "payment_frequency", "section": "Tarification", "type": "radio",
         "question": "Fréquence de paiement souhaitée",
         "options": [{"label": "Mensuelle", "value": "monthly"}, {"label": "Annuelle", "value": "yearly"}]},
    ],
}


def get_catalog(template: str) -> list[dict]:
    return CATALOGS.get(template, [])


def get_catalog_entry(template: str, key: str) -> dict | None:
    return next((e for e in get_catalog(template) if e["key"] == key), None)
