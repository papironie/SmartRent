"""Landlord pages: dashboard, tenants, leases, billing, inquiries, analytics.

Every query is limited to the signed-in landlord: Property.owner_id for anything reached through a
property, Tenant.landlord_id for tenants. Another landlord's records answer 404 (see ownership.py).
"""
import secrets

from dateutil.relativedelta import relativedelta
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from .. import ownership
from ..auth_helpers import role_required
from ..extensions import db
from ..models import Charge, Inquiry, Lease, Property, Tenant, Unit, User
from ..services import analytics, billing, notify, tenant_status
from ..utils import month_start, parse_date, parse_month, to_float, today

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
@role_required("landlord")
def _guard():
    """Every route in this blueprint is landlord-only."""


def _mine():
    """Condition for queries that join Property: only the signed-in landlord's rows."""
    return Property.owner_id == ownership.me()


# ---------------- dashboard ----------------
@bp.route("/", methods=["GET", "POST"])
def dashboard():
    me = ownership.me()
    if request.method == "POST":
        r = billing.daily_tasks(owner_id=me)
        flash(f"Daily tasks done: {r['charges']} new charges, {r['reminders']} reminders, {r['leases']} lease alerts.", "success")
        return redirect(url_for("admin.dashboard"))

    now = today()
    scope = ownership.property_ids()
    upcoming = db.session.execute(
        db.select(Charge, User.name, Tenant.id, Unit.unit_no, Property.name)
        .join(Lease, Lease.id == Charge.lease_id).join(Tenant, Tenant.id == Lease.tenant_id)
        .join(User, User.id == Tenant.user_id).join(Unit, Unit.id == Lease.unit_id)
        .join(Property, Property.id == Unit.property_id)
        .where(_mine(), Charge.due_date.between(now, now + relativedelta(days=7)))
        .order_by(Charge.due_date).limit(8)
    ).all()
    ending = db.session.execute(
        db.select(Lease, User.name, Unit.unit_no, Property.name)
        .join(Tenant, Tenant.id == Lease.tenant_id).join(User, User.id == Tenant.user_id)
        .join(Unit, Unit.id == Lease.unit_id).join(Property, Property.id == Unit.property_id)
        .where(_mine(), Lease.status == "active",
               Lease.end_date <= now + relativedelta(days=current_app.config["LEASE_WARN_DAYS"]))
        .order_by(Lease.end_date).limit(8)
    ).all()
    return render_template(
        "admin/dashboard.html", title="Dashboard", k=analytics.kpis(scope),
        insights=analytics.insights(scope)[:4], upcoming=upcoming, ending=ending, charts=True,
    )


# ---------------- tenants ----------------
@bp.route("/tenants", methods=["GET", "POST"])
def tenants():
    me = ownership.me()
    if request.method == "POST":
        f = request.form
        name, email, phone = f.get("name", "").strip(), f.get("email", "").strip().lower(), f.get("phone", "").strip()
        if not name or "@" not in email or "." not in email.split("@")[-1] or not phone:
            flash("Name, a valid email, and phone are required.", "danger")
            return redirect(url_for("admin.tenants"))
        if db.session.scalar(db.select(User.id).where(User.email == email)):
            flash("That email already has an account.", "danger")
            return redirect(url_for("admin.tenants"))
        temp = secrets.token_urlsafe(8)
        user = User(name=name, email=email, role="tenant")
        user.set_password(temp)
        tenant = Tenant(user=user, landlord_id=me, phone=phone,
                        emergency_name=f.get("emergency_name", "").strip() or None,
                        emergency_phone=f.get("emergency_phone", "").strip() or None)
        db.session.add_all([user, tenant])  # both rows or neither: one transaction
        db.session.commit()
        notify.send_email(email, "Your SmartRent account", f"Sign in with {email} and temporary password {temp}")
        flash(f"Tenant added. Temporary password: {temp} (shown once, also emailed). Next: create their lease.", "success")
        return redirect(url_for("admin.tenant_detail", tenant_id=tenant.id))

    search = request.args.get("q", "").strip()
    flt = request.args.get("status", "")
    q = (
        db.select(Tenant, User, Unit.unit_no, Property.name, Lease.end_date)
        .join(User, User.id == Tenant.user_id)
        .outerjoin(Lease, db.and_(Lease.tenant_id == Tenant.id, Lease.status == "active"))
        .outerjoin(Unit, Unit.id == Lease.unit_id)
        .outerjoin(Property, Property.id == Unit.property_id)
        .where(Tenant.landlord_id == me)
        .order_by(User.name)
    )
    if search:
        like = f"%{search}%"
        q = q.where(db.or_(User.name.ilike(like), User.email.ilike(like)))
    statuses = tenant_status.all_statuses(me)
    rows = [r for r in db.session.execute(q).all() if not flt or statuses.get(r[0].id) == flt]
    return render_template("admin/tenants.html", title="Tenants", rows=rows, statuses=statuses,
                           search=search, flt=flt, labels=tenant_status.LABELS, badge=tenant_status.BADGE)


@bp.get("/tenants/<int:tenant_id>")
def tenant_detail(tenant_id):
    t = ownership.own_tenant(tenant_id)
    lease_map = tenant_status.active_lease_map(ownership.me())
    status = tenant_status.classify(lease_map.get(tenant_id))
    charges = db.session.execute(
        db.select(Charge, Unit.unit_no, Property.name)
        .join(Lease, Lease.id == Charge.lease_id).join(Unit, Unit.id == Lease.unit_id)
        .join(Property, Property.id == Unit.property_id)
        .where(Lease.tenant_id == tenant_id).order_by(Charge.period.desc())
    ).all()
    leases = db.session.execute(
        db.select(Lease, Unit.unit_no, Property.name).join(Unit, Unit.id == Lease.unit_id)
        .join(Property, Property.id == Unit.property_id)
        .where(Lease.tenant_id == tenant_id).order_by(Lease.start_date.desc())
    ).all()
    return render_template("admin/tenant_detail.html", title=t.user.name, t=t, status=status, charges=charges,
                           leases=leases, labels=tenant_status.LABELS, badge=tenant_status.BADGE)


# ---------------- leases ----------------
@bp.route("/leases", methods=["GET", "POST"])
def leases():
    now = today()
    me = ownership.me()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            return _create_lease()
        lease = ownership.own_lease(int(request.form.get("lease_id") or 0))
        if lease.status != "active":
            flash("Lease not found or already ended.", "danger")
            return redirect(url_for("admin.leases"))
        if action == "renew":
            months = max(1, min(24, int(to_float(request.form.get("months"), 12))))
            lease.end_date = lease.end_date + relativedelta(months=months)
            db.session.commit()
            flash(f"Lease extended by {months} month(s).", "success")
        elif action == "end":
            lease.status = "ended"
            lease.unit.status = "vacant"
            db.session.commit()
            flash("Lease ended. Unit is vacant. Past charges stay on the tenant record.", "success")
        return redirect(url_for("admin.leases"))

    show = request.args.get("show", "active")
    warn = now + relativedelta(days=current_app.config["LEASE_WARN_DAYS"])
    q = (
        db.select(Lease, User.name, Unit.unit_no, Property.name)
        .join(Tenant, Tenant.id == Lease.tenant_id).join(User, User.id == Tenant.user_id)
        .join(Unit, Unit.id == Lease.unit_id).join(Property, Property.id == Unit.property_id)
        .where(_mine())
        .order_by(Lease.end_date)
    )
    if show != "all":
        q = q.where(Lease.status == "active")
    rows = []
    for lease, tenant, unit_no, prop in db.session.execute(q).all():
        if lease.status == "ended":
            state = "ended"
        elif lease.end_date < now:
            state = "expired"
        elif lease.end_date <= warn:
            state = "ending"
        else:
            state = "active"
        rows.append((lease, tenant, unit_no, prop, state))

    free_tenants = db.session.execute(
        db.select(Tenant.id, User.name).join(User, User.id == Tenant.user_id)
        .where(Tenant.landlord_id == me,
               ~Tenant.id.in_(db.select(Lease.tenant_id).where(Lease.status == "active"))).order_by(User.name)
    ).all()
    vacant = db.session.execute(
        db.select(Unit.id, Unit.unit_no, Unit.monthly_rent, Property.name)
        .join(Property, Property.id == Unit.property_id).where(_mine(), Unit.status == "vacant")
        .order_by(Property.name, Unit.unit_no)
    ).all()
    return render_template(
        "admin/leases.html", title="Leases", rows=rows, show=show, free_tenants=free_tenants, vacant=vacant,
        warn_days=current_app.config["LEASE_WARN_DAYS"], default_start=month_start(now),
        default_end=month_start(now) + relativedelta(years=1, days=-1),
        preselect=request.args.get("tenant_id", type=int),
    )


def _create_lease():
    me = ownership.me()
    f = request.form
    tid, uid = int(to_float(f.get("tenant_id"))), int(to_float(f.get("unit_id")))
    start, end = parse_date(f.get("start_date")), parse_date(f.get("end_date"))
    deposit, due_day = to_float(f.get("deposit")), int(to_float(f.get("due_day")))
    err = None
    if not tid or not uid:
        err = "Pick a tenant and a unit."
    elif not start or not end:
        err = "Enter valid start and end dates."
    elif end <= start:
        err = "End date must be after the start date."
    elif not 1 <= due_day <= 31:
        err = "Due day must be between 1 and 31."
    elif deposit < 0:
        err = "Deposit cannot be negative."
    if not err:
        unit = db.session.scalar(db.select(Unit).where(Unit.id == uid).with_for_update())  # row lock
        tenant = db.session.get(Tenant, tid)
        busy = db.session.scalar(db.select(Lease.id).where(Lease.tenant_id == tid, Lease.status == "active"))
        if unit is None or unit.property.owner_id != me:
            err = "Unit not found."
        elif tenant is None or tenant.landlord_id != me:
            err = "Tenant not found."
        elif unit.status != "vacant":
            err = "That unit is not vacant anymore."
        elif busy:
            err = "This tenant already has an active lease."
        else:
            raw = f.get("monthly_rent", "").strip()
            rent = to_float(raw) if raw else float(unit.monthly_rent)
            if rent <= 0:
                err = "Rent must be above zero."
    if err:
        db.session.rollback()
        flash(err, "danger")
        return redirect(url_for("admin.leases"))
    try:
        db.session.add(Lease(tenant_id=tid, unit_id=uid, start_date=start, end_date=end, monthly_rent=rent,
                             deposit=deposit, due_day=due_day))
        unit.status = "occupied"
        db.session.commit()
    except IntegrityError:  # the partial unique indexes caught a race
        db.session.rollback()
        flash("That unit or tenant was just leased in another window. Refresh and try again.", "danger")
        return redirect(url_for("admin.leases"))
    n = billing.generate(today(), owner_id=me)
    flash("Lease created." + (f" {n} charge(s) generated for this month." if n else ""), "success")
    return redirect(url_for("admin.leases"))


# ---------------- billing ----------------
@bp.route("/charges", methods=["GET", "POST"])
def charges():
    now = today()
    month = parse_month(request.values.get("month")) or month_start(now)
    if request.method == "POST":
        if month > month_start(now):
            flash("You can only bill the current or past months.", "danger")
        else:
            n = billing.generate(month, owner_id=ownership.me())
            flash(f"{n} new charge(s) created for {month:%b %Y}." if n
                  else f"All active leases are already billed for {month:%b %Y}.", "success")
        return redirect(url_for("admin.charges", month=f"{month:%Y-%m}"))

    rows = db.session.execute(
        db.select(Charge, User.name, Tenant.id, Unit.unit_no, Property.name)
        .join(Lease, Lease.id == Charge.lease_id).join(Tenant, Tenant.id == Lease.tenant_id)
        .join(User, User.id == Tenant.user_id).join(Unit, Unit.id == Lease.unit_id)
        .join(Property, Property.id == Unit.property_id)
        .where(_mine(), Charge.period == month).order_by(Charge.due_date, User.name)
    ).all()
    total = sum(float(r[0].amount) for r in rows)
    return render_template("admin/charges.html", title="Billing", rows=rows, month=month, total=total)


# ---------------- inquiries (from guests on the public listings) ----------------
INQUIRY_STATUSES = {"new": "New", "contacted": "Contacted", "closed": "Closed"}


@bp.route("/inquiries", methods=["GET", "POST"])
def inquiries():
    if request.method == "POST":
        inq = db.session.get(Inquiry, int(to_float(request.form.get("inquiry_id"))))
        if inq is None or inq.unit.property.owner_id != ownership.me():
            abort(404)
        status = request.form.get("status")
        if status not in INQUIRY_STATUSES:
            abort(400)
        inq.status = status
        db.session.commit()
        flash(f"Inquiry marked {INQUIRY_STATUSES[status].lower()}.", "success")
        return redirect(url_for("admin.inquiries", status=request.args.get("status", "")))
    flt = request.args.get("status", "")
    q = (db.select(Inquiry, User.name, User.email, Unit.unit_no, Unit.status, Property.name)
         .join(User, User.id == Inquiry.user_id).join(Unit, Unit.id == Inquiry.unit_id)
         .join(Property, Property.id == Unit.property_id).where(_mine()).order_by(Inquiry.created_at.desc()))
    if flt in INQUIRY_STATUSES:
        q = q.where(Inquiry.status == flt)
    counts = dict(db.session.execute(
        db.select(Inquiry.status, db.func.count(Inquiry.id)).join(Unit, Unit.id == Inquiry.unit_id)
        .join(Property, Property.id == Unit.property_id).where(_mine()).group_by(Inquiry.status)).all())
    return render_template("admin/inquiries.html", title="Inquiries", rows=db.session.execute(q).all(),
                           flt=flt, statuses=INQUIRY_STATUSES, counts=counts)


# ---------------- analytics ----------------
@bp.get("/analytics", endpoint="analytics")
def analytics_view():
    pid, scope = ownership.scope(request.args.get("property"))
    statuses = tenant_status.all_statuses(ownership.me())
    counts: dict[str, int] = {}
    for s in statuses.values():
        counts[s] = counts.get(s, 0) + 1
    perf = analytics.unit_performance(scope)
    props = db.session.execute(db.select(Property.id, Property.name).where(_mine()).order_by(Property.name)).all()
    return render_template("admin/analytics.html", title="Analytics", charts=True, insights=analytics.insights(scope),
                           k=analytics.kpis(scope), units=analytics.records(perf), demand=analytics.demand(scope),
                           rent_stats=analytics.rent_stats(perf), props=props, pid=pid, counts=counts,
                           labels=tenant_status.LABELS, badge=tenant_status.BADGE)
