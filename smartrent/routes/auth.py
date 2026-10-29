import time
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..auth_helpers import current_user
from ..extensions import db
from ..models import Tenant, User

bp = Blueprint("auth", __name__)
MIN_PASSWORD = 8


@bp.before_app_request
def _refresh_account():
    """Re-read role and active flag every request, so a deactivation or role change applies at once."""
    u = current_user()
    if not u or request.endpoint == "static":
        return
    row = db.session.execute(db.select(User.role, User.is_active).where(User.id == u["id"])).first()
    if row is None or not row.is_active:
        session.clear()
    elif row.role != u["role"]:
        session["user"] = {**u, "role": row.role}


@bp.get("/")
def home():
    """Visitors and guests land on the public listings; everyone else on their own start page."""
    u = current_user()
    start = {"superadmin": "superadmin.overview", "landlord": "admin.dashboard", "tenant": "tenant.dashboard"}
    return redirect(url_for(start.get(u["role"], "listings.index") if u else "listings.index"))


def _start_session(user: User) -> None:
    session.clear()  # fresh session on login: no session fixation
    tenant_id = None
    if user.role == "tenant":
        tenant_id = db.session.scalar(db.select(Tenant.id).where(Tenant.user_id == user.id))
    session["user"] = {"id": user.id, "name": user.name, "role": user.role, "tenant_id": tenant_id}
    user.last_login_at = datetime.now()
    db.session.commit()


def _safe_next(raw: str | None) -> str | None:
    """Only follow ?next= to a path on this site (stops open redirects)."""
    return raw if raw and raw.startswith("/") and not raw.startswith("//") else None


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    nxt = _safe_next(request.values.get("next"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = db.session.scalar(db.select(User).where(User.email == email))
        if user and user.check_password(request.form.get("password", "")):
            if not user.is_active:
                error = "This account is deactivated. Contact the administrator."
            else:
                _start_session(user)
                return redirect(nxt or url_for("auth.home"))
        else:
            time.sleep(0.4)  # slows password guessing a little. Real fix: lockout after N tries.
            error = "Email or password is wrong."
    return render_template("login.html", error=error, email=request.form.get("email", ""), next=nxt)


@bp.route("/register", methods=["GET", "POST"])
def register():
    """Anyone can create a guest account to send inquiries. Landlords are created by the superadmin,
    tenants by their landlord."""
    errors: list[str] = []
    f = request.form
    nxt = _safe_next(request.values.get("next"))
    if request.method == "POST":
        name, email = f.get("name", "").strip(), f.get("email", "").strip().lower()
        pw = f.get("password", "")
        if not name:
            errors.append("Name is required.")
        if "@" not in email or "." not in email.split("@")[-1]:
            errors.append("Enter a valid email.")
        elif db.session.scalar(db.select(User.id).where(User.email == email)):
            errors.append("That email already has an account. Sign in instead.")
        if len(pw) < MIN_PASSWORD:
            errors.append(f"Password must be at least {MIN_PASSWORD} characters.")
        elif pw != f.get("confirm", ""):
            errors.append("Passwords do not match.")
        if not errors:
            user = User(name=name[:100], email=email, role="guest")
            user.set_password(pw)
            db.session.add(user)
            db.session.commit()
            _start_session(user)
            flash("Welcome! You can now send inquiries about available units.", "success")
            return redirect(nxt or url_for("listings.index"))
    return render_template("register.html", errors=errors, form=f, next=nxt, min_password=MIN_PASSWORD)


@bp.post("/logout")
def logout():
    session.clear()
    flash("Signed out.", "success")
    return redirect(url_for("auth.login"))
