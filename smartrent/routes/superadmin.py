"""Superadmin: runs the platform. Platform overview (every landlord's numbers) and user accounts.

The superadmin never manages properties, tenants or billing; that is each landlord's own business.
Safety rules: you cannot deactivate or delete yourself, and the last active superadmin cannot be deactivated.
"""
import secrets

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from .. import ownership
from ..auth_helpers import role_required
from ..extensions import db
from ..models import ROLES, Inquiry, Property, Tenant, Unit, User
from ..services import analytics, notify

bp = Blueprint("superadmin", __name__, url_prefix="/superadmin")

ROLE_LABELS = {"superadmin": "Superadmin", "landlord": "Landlord", "tenant": "Tenant", "guest": "Guest"}
ROLE_BADGE = {"superadmin": "dark", "landlord": "primary", "tenant": "success", "guest": "secondary"}
CREATABLE = ("landlord", "superadmin")  # tenants are created by their landlord; guests register themselves


@bp.before_request
@role_required("superadmin")
def _guard():
    """Superadmin only. Landlords get 403."""


# ---------------- platform overview ----------------
@bp.get("/")
def overview():
    landlords = db.session.scalars(db.select(User).where(User.role == "landlord").order_by(User.name)).all()
    open_by_owner = dict(db.session.execute(
        db.select(Property.owner_id, db.func.count(Inquiry.id)).join(Unit, Unit.id == Inquiry.unit_id)
        .join(Property, Property.id == Unit.property_id).where(Inquiry.status == "new").group_by(Property.owner_id)
    ).all())
    tenants_by_owner = dict(db.session.execute(
        db.select(Tenant.landlord_id, db.func.count(Tenant.id)).group_by(Tenant.landlord_id)).all())
    rows = []
    for u in landlords:
        ids = ownership.property_ids(u.id)
        k = analytics.kpis(ids)
        rows.append({"user": u, "properties": len(ids), "tenants": tenants_by_owner.get(u.id, 0),
                     "open_inquiries": open_by_owner.get(u.id, 0), **k})
    rows.sort(key=lambda r: -r["rent_roll"])
    total = analytics.kpis(None)
    roles = dict(db.session.execute(db.select(User.role, db.func.count(User.id)).group_by(User.role)).all())
    demand = analytics.demand(None)
    return render_template("superadmin/overview.html", title="Platform overview", rows=rows, total=total,
                           roles=roles, demand=demand, properties=sum(r["properties"] for r in rows))


# ---------------- user accounts ----------------
def _active_superadmins() -> int:
    return db.session.scalar(db.select(db.func.count(User.id)).where(User.role == "superadmin", User.is_active))


def _back():
    return redirect(url_for("superadmin.users", role=request.args.get("role", "")))


@bp.route("/users", methods=["GET", "POST"])
def users():
    if request.method == "POST":
        return _handle_post()
    flt = request.args.get("role", "")
    q = db.select(User).order_by(User.role, User.name)
    if flt in ROLES:
        q = q.where(User.role == flt)
    counts = dict(db.session.execute(db.select(User.role, db.func.count(User.id)).group_by(User.role)).all())
    return render_template("superadmin/users.html", title="User accounts", rows=db.session.scalars(q).all(),
                           flt=flt, counts=counts, labels=ROLE_LABELS, badge=ROLE_BADGE, me_id=session["user"]["id"])


def _handle_post():
    f = request.form
    action = f.get("action")
    if action == "create":
        name, email, role = f.get("name", "").strip(), f.get("email", "").strip().lower(), f.get("role", "landlord")
        if not name or "@" not in email or role not in CREATABLE:
            flash("Name, a valid email and a role (landlord or superadmin) are required.", "danger")
        elif db.session.scalar(db.select(User.id).where(User.email == email)):
            flash("That email already has an account.", "danger")
        else:
            temp = secrets.token_urlsafe(8)
            user = User(name=name[:100], email=email, role=role)
            user.set_password(temp)
            db.session.add(user)
            db.session.commit()
            notify.send_email(email, "Your SmartRent account", f"Sign in with {email} and temporary password {temp}")
            flash(f"{ROLE_LABELS[role]} account created. Temporary password: {temp} (shown once, also emailed).", "success")
        return _back()

    user = db.session.get(User, int(f.get("user_id", 0) or 0)) or abort(404)
    is_me = user.id == session["user"]["id"]
    last_super = user.role == "superadmin" and user.is_active and _active_superadmins() <= 1

    if action == "toggle_active":
        if is_me:
            flash("You cannot deactivate yourself.", "danger")
        elif last_super:
            flash("This is the last active superadmin. Create another one first.", "danger")
        else:
            user.is_active = not user.is_active
            db.session.commit()
            extra = " Their units are hidden from the public listings." if user.role == "landlord" and not user.is_active else ""
            flash(f"{user.name} {'reactivated' if user.is_active else 'deactivated (signed out everywhere)'}.{extra}", "success")
    elif action == "reset_password":
        temp = secrets.token_urlsafe(8)
        user.set_password(temp)
        db.session.commit()
        notify.send_email(user.email, "Your SmartRent password was reset", f"Your temporary password is {temp}")
        flash(f"New temporary password for {user.name}: {temp} (shown once, also emailed).", "success")
    elif action == "delete_guest":
        if user.role != "guest":
            flash("Only guest accounts can be deleted. Deactivate other accounts instead.", "danger")
        else:
            db.session.delete(user)  # their inquiries go too (ON DELETE CASCADE)
            db.session.commit()
            flash("Guest account deleted.", "success")
    else:
        abort(400)
    return _back()
