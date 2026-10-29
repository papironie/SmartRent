"""Session auth. Hiding a menu link is not security; the decorators are."""
from functools import wraps

from flask import abort, redirect, session, url_for

from .extensions import db
from .models import Inquiry, Notification, Property, Unit


def current_user() -> dict | None:
    return session.get("user")


def login_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        if not current_user():
            return redirect(url_for("auth.login"))
        return view(*a, **kw)

    return wrapper


def role_required(*roles: str):
    """Put on EVERY page and API route. 401-style redirect if signed out, 403 if wrong role.
    Roles do not inherit: a superadmin runs the platform and cannot open landlord pages."""

    def deco(view):
        @wraps(view)
        def wrapper(*a, **kw):
            u = current_user()
            if not u:
                return redirect(url_for("auth.login"))
            if u["role"] not in roles:
                abort(403)
            return view(*a, **kw)

        return wrapper

    return deco


def nav_for(user: dict | None) -> list[tuple[str, str, str]]:
    """(endpoint, label, badge). Computed per request for the sidebar."""
    if not user:
        return [("listings.index", "Units for rent", "")]
    role = user["role"]
    if role == "superadmin":
        return [
            ("superadmin.overview", "Platform overview", ""),
            ("superadmin.users", "User accounts", ""),
            ("listings.index", "Public listings", ""),
        ]
    if role == "landlord":
        new = db.session.scalar(
            db.select(db.func.count(Inquiry.id)).join(Unit, Unit.id == Inquiry.unit_id)
            .join(Property, Property.id == Unit.property_id)
            .where(Inquiry.status == "new", Property.owner_id == user["id"])
        )
        return [
            ("admin.dashboard", "Dashboard", ""),
            ("properties.index", "Properties & units", ""),
            ("admin.tenants", "Tenants", ""),
            ("admin.leases", "Leases", ""),
            ("admin.charges", "Billing", ""),
            ("admin.inquiries", "Inquiries", str(new) if new else ""),
            ("admin.analytics", "Analytics", ""),
            ("reports.index", "Reports", ""),
            ("listings.index", "Public listings", ""),
        ]
    if role == "guest":
        return [("listings.index", "Units for rent", ""), ("listings.my_inquiries", "My inquiries", "")]
    unread = db.session.scalar(
        db.select(db.func.count(Notification.id)).where(Notification.user_id == user["id"], Notification.read_at.is_(None))
    )
    return [("tenant.dashboard", "My account", str(unread) if unread else ""), ("listings.index", "Units for rent", "")]
