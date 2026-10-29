"""Public listings. Anyone can browse VACANT units of every active landlord; a signed-in guest can send an
inquiry, which goes to the unit's landlord. Every detail-page view is logged (unit_views) for demand analytics."""
from flask import (Blueprint, abort, flash, redirect, render_template, request, send_from_directory,
                   session, url_for)
from sqlalchemy.orm import selectinload

from ..auth_helpers import current_user, role_required
from ..extensions import db
from ..models import Inquiry, Property, Unit, UnitView, User
from ..services import notify, photos
from ..utils import parse_date, to_float, today

bp = Blueprint("listings", __name__)

SORTS = {
    "rent_asc": ("Rent: low to high", Unit.monthly_rent.asc()),
    "rent_desc": ("Rent: high to low", Unit.monthly_rent.desc()),
    "property": ("Property", Property.name.asc()),
}
INQUIRY_LABELS = {"new": "Sent", "contacted": "Contacted", "closed": "Closed"}
INQUIRY_BADGE = {"new": "info", "contacted": "success", "closed": "secondary"}


def _listed():
    """Units on the market: vacant, and the landlord's account is active (deactivated landlords disappear)."""
    return db.and_(Unit.status == "vacant", Property.owner_id.in_(db.select(User.id).where(User.is_active)))


def _can_preview(me, unit) -> bool:
    """The unit's own landlord and the superadmin may open an unlisted unit; their visits are not counted."""
    return bool(me) and (me["role"] == "superadmin" or (me["role"] == "landlord" and unit.property.owner_id == me["id"]))


@bp.get("/units")
def index():
    a = request.args
    pid = a.get("property", type=int)
    lo, hi = to_float(a.get("min_rent"), 0), to_float(a.get("max_rent"), 0)
    sort = a.get("sort") if a.get("sort") in SORTS else "rent_asc"
    q = (db.select(Unit).join(Property, Property.id == Unit.property_id)
         .options(selectinload(Unit.photos), selectinload(Unit.property))
         .where(_listed()))
    if pid:
        q = q.where(Unit.property_id == pid)
    if lo > 0:
        q = q.where(Unit.monthly_rent >= lo)
    if hi > 0:
        q = q.where(Unit.monthly_rent <= hi)
    units = db.session.scalars(q.order_by(SORTS[sort][1], Unit.unit_no)).all()
    props = db.session.execute(
        db.select(Property.id, Property.name, db.func.count(Unit.id))
        .join(Unit, Unit.property_id == Property.id).where(_listed())
        .group_by(Property.id).order_by(Property.name)
    ).all()
    return render_template("listings/index.html", title="Units for rent", units=units, props=props,
                           pid=pid, lo=a.get("min_rent", ""), hi=a.get("max_rent", ""), sort=sort, sorts=SORTS)


@bp.get("/units/<int:unit_id>")
def detail(unit_id):
    unit = db.session.get(Unit, unit_id) or abort(404)
    me = current_user()
    preview = _can_preview(me, unit)
    listed = unit.status == "vacant" and unit.property.owner.is_active
    if not listed and not preview:
        abort(404)  # occupied units are not on the market; do not reveal them
    if not preview and not (me and me["role"] == "landlord"):  # landlord clicks would distort demand numbers
        db.session.add(UnitView(unit_id=unit.id, user_id=me["id"] if me else None))
        db.session.commit()
    others = db.session.scalars(
        db.select(Unit).join(Property, Property.id == Unit.property_id).options(selectinload(Unit.photos))
        .where(Unit.property_id == unit.property_id, _listed(), Unit.id != unit.id)
        .order_by(Unit.monthly_rent).limit(4)
    ).all()
    open_inquiry = None
    if me and me["role"] == "guest":
        open_inquiry = db.session.scalar(db.select(Inquiry).where(
            Inquiry.unit_id == unit.id, Inquiry.user_id == me["id"], Inquiry.status != "closed"))
    return render_template("listings/detail.html", title=f"{unit.property.name} {unit.unit_no}", u=unit, p=unit.property,
                           others=others, open_inquiry=open_inquiry, listed=listed, maps=unit.property.lat is not None)


@bp.post("/units/<int:unit_id>/inquire")
@role_required("guest")
def inquire(unit_id):
    unit = db.session.get(Unit, unit_id) or abort(404)
    if unit.status != "vacant" or not unit.property.owner.is_active:
        abort(404)
    me = session["user"]
    f = request.form
    message, phone = f.get("message", "").strip(), f.get("phone", "").strip()
    viewing = parse_date(f.get("viewing_date"))
    back = redirect(url_for("listings.detail", unit_id=unit.id))
    if not message or len(message) > 1000:
        flash("Write a message (up to 1000 characters).", "danger")
        return back
    if f.get("viewing_date") and (viewing is None or viewing < today()):
        flash("The viewing date must be today or later.", "danger")
        return back
    if db.session.scalar(db.select(Inquiry.id).where(
            Inquiry.unit_id == unit.id, Inquiry.user_id == me["id"], Inquiry.status != "closed")):
        flash("You already have an open inquiry for this unit. The admin will contact you.", "warning")
        return back
    inq = Inquiry(unit_id=unit.id, user_id=me["id"], message=message, phone=phone[:30] or None, viewing_date=viewing)
    db.session.add(inq)
    db.session.flush()
    notify.create(unit.property.owner_id, "inquiry", f"{me['name']} asked about {unit.property.name} {unit.unit_no}.",
                  f"inquiry:{inq.id}")
    db.session.commit()
    flash("Inquiry sent. The admin will contact you by email or phone.", "success")
    return back


@bp.get("/my-inquiries")
@role_required("guest")
def my_inquiries():
    rows = db.session.scalars(
        db.select(Inquiry).options(selectinload(Inquiry.unit).selectinload(Unit.property))
        .where(Inquiry.user_id == session["user"]["id"]).order_by(Inquiry.created_at.desc())
    ).all()
    return render_template("listings/my_inquiries.html", title="My inquiries", rows=rows,
                           labels=INQUIRY_LABELS, badge=INQUIRY_BADGE)


@bp.get("/media/units/<path:filename>")
def photo(filename):
    """Public: listing photos are meant to be seen. send_from_directory refuses paths outside the folder."""
    return send_from_directory(photos.folder(), filename, max_age=86400)
