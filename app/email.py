import logging
import smtplib
from datetime import date
from email.message import EmailMessage
from app.config import settings

logger = logging.getLogger("app.email")

_DAYS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MONTHS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def format_date_fr(iso_date: str) -> str:
    d = date.fromisoformat(iso_date)
    return f"{_DAYS_FR[d.weekday()]} {d.day} {_MONTHS_FR[d.month - 1]} {d.year}"


def send_email(to: str, subject: str, html_body: str) -> bool:
    """Sends a single HTML email over SMTP. No-ops (logs and returns False)
    when SMTP isn't configured yet or there's no recipient, so callers never
    need to guard against email being unset — see settings.smtp_host in
    app/config.py. Any send failure is caught and logged rather than raised,
    so a broken mail server never breaks lead/booking creation."""
    if not settings.smtp_host or not to:
        logger.info("Email skipped (SMTP not configured or no recipient): %r -> %r", subject, to)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.email_from
    msg["To"] = to
    msg.set_content("Ce message nécessite un client de messagerie compatible HTML.")
    msg.add_alternative(html_body, subtype="html")

    try:
        # Port 465 is implicit TLS (needs SMTP_SSL from the first byte);
        # anything else (587, 25...) starts plaintext and upgrades via
        # STARTTLS — Hostinger/Titan mail offers both, so both are supported
        # rather than assuming one.
        smtp_cls = smtplib.SMTP_SSL if settings.smtp_port == 465 else smtplib.SMTP
        with smtp_cls(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            if settings.smtp_use_tls and settings.smtp_port != 465:
                server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to)
        return False


def _wrap(title: str, body_html: str) -> str:
    return f"""
    <div style="font-family: Arial, Helvetica, sans-serif; max-width: 480px; margin: 0 auto; color: #111111;">
      <h2 style="color: #0f2a4a; margin-bottom: 16px;">{title}</h2>
      {body_html}
      <p style="margin-top: 32px; font-size: 12px; color: #888888;">
        New World Courtage — 07 45 89 18 65 — devis@newworldcourtage.com
      </p>
    </div>
    """


def send_lead_confirmation_email(name: str, email: str, lead_type_label: str) -> bool:
    body = f"""
      <p>Bonjour {name},</p>
      <p>Nous avons bien reçu votre demande de devis <strong>{lead_type_label}</strong>.
      Un de nos experts va l'étudier et vous contactera dans les plus brefs délais.</p>
    """
    return send_email(email, "Votre demande a bien été reçue — New World Courtage", _wrap("Demande reçue", body))


def send_booking_confirmation_email(name: str, email: str, iso_date: str, time: str) -> bool:
    body = f"""
      <p>Bonjour {name},</p>
      <p>Votre rendez-vous est confirmé : nous vous appellerons le
      <strong>{format_date_fr(iso_date)}</strong> à <strong>{time}</strong>.</p>
    """
    return send_email(email, "Votre rendez-vous est confirmé — New World Courtage", _wrap("Rendez-vous confirmé", body))


def send_account_welcome_email(name: str, email: str, account_type: str) -> bool:
    space_label = "espace partenaire" if account_type == "partenaire" else "espace client"
    body = f"""
      <p>Bonjour {name},</p>
      <p>Votre compte New World Courtage a bien été créé. Vous pouvez désormais accéder à votre
      {space_label} pour {"suivre vos demandes de devis" if account_type != "partenaire" else "retrouver votre lien de parrainage"}.</p>
    """
    return send_email(email, "Bienvenue chez New World Courtage", _wrap("Compte créé", body))


def send_account_password_reset_email(name: str, email: str, reset_url: str) -> bool:
    body = f"""
      <p>Bonjour {name},</p>
      <p>Vous avez demandé la réinitialisation de votre mot de passe. Ce lien est valable 1 heure :</p>
      <p><a href="{reset_url}" style="color: #0f2a4a;">Réinitialiser mon mot de passe</a></p>
      <p>Si vous n'êtes pas à l'origine de cette demande, vous pouvez ignorer cet email.</p>
    """
    return send_email(email, "Réinitialisation de votre mot de passe — New World Courtage", _wrap("Mot de passe oublié", body))


# Sent to the OLD email address after an email/password change succeeds —
# a tripwire, not a confirmation: the account owner sees it even if the
# change wasn't actually them (e.g. a leaked session token was used to
# hijack the account), since it's the one address the attacker doesn't
# control. See routers/accounts.py's update_me/change_password.
def send_account_email_changed_notice(name: str, old_email: str, new_email: str) -> bool:
    body = f"""
      <p>Bonjour {name},</p>
      <p>L'adresse email de votre compte New World Courtage vient d'être changée pour
      <strong>{new_email}</strong>.</p>
      <p>Si vous êtes à l'origine de ce changement, vous pouvez ignorer cet email.
      Si ce n'est pas le cas, contactez-nous immédiatement à devis@newworldcourtage.com.</p>
    """
    return send_email(old_email, "Votre adresse email a été modifiée — New World Courtage", _wrap("Adresse email modifiée", body))


def send_account_password_changed_notice(name: str, email: str) -> bool:
    body = f"""
      <p>Bonjour {name},</p>
      <p>Le mot de passe de votre compte New World Courtage vient d'être changé.</p>
      <p>Si vous êtes à l'origine de ce changement, vous pouvez ignorer cet email.
      Si ce n'est pas le cas, contactez-nous immédiatement à devis@newworldcourtage.com.</p>
    """
    return send_email(email, "Votre mot de passe a été modifié — New World Courtage", _wrap("Mot de passe modifié", body))
