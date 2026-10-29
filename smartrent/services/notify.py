"""In-app notifications + email (integration point: SMTP, falls back to a log file)."""
import smtplib
from datetime import datetime
from email.message import EmailMessage

from flask import current_app
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..extensions import db
from ..models import Notification, User


def create(user_id: int, type_: str, message: str, dedupe_key: str | None = None) -> bool:
    """Same dedupe_key twice = ignored (safe for the daily job). Returns True if newly created."""
    stmt = (
        pg_insert(Notification)
        .values(user_id=user_id, type=type_, message=message, dedupe_key=dedupe_key)
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(Notification.id)
    )
    created = db.session.execute(stmt).first() is not None
    if created:
        email = db.session.scalar(db.select(User.email).where(User.id == user_id))
        if email:
            send_email(email, "SmartRent: " + type_.replace("_", " "), message)
    return created


def send_email(to: str, subject: str, body: str) -> None:
    cfg = current_app.config
    if cfg["MAIL_ENABLED"]:
        try:
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = cfg["MAIL_FROM"], to, subject
            msg.set_content(body)
            with smtplib.SMTP(cfg["MAIL_HOST"], cfg["MAIL_PORT"], timeout=10) as s:
                s.starttls()
                if cfg["MAIL_USER"]:
                    s.login(cfg["MAIL_USER"], cfg["MAIL_PASS"])
                s.send_message(msg)
            return
        except Exception as ex:  # never let mail break the request
            body += f"\n[SMTP failed: {ex}]"
    try:
        with open(cfg["MAIL_LOG"], "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] To: {to} | {subject} | {body}\n")
    except OSError:
        pass
