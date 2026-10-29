"""Properties & units. Reference CRUD page; also the PostGIS / Leaflet / Excel-import module."""
import sqlalchemy as sa
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from geoalchemy2 import Geography
from io import BytesIO
from sqlalchemy.exc import IntegrityError

from .. import ownership
from ..auth_helpers import role_required
from ..extensions import db
from ..models import Lease, Property, Tenant, Unit, UnitPhoto, User
from ..services import excel, photos
from ..utils import to_float

bp = Blueprint("properties", __name__, url_prefix="/admin/properties")


@bp.before_request
@role_required("landlord")
def _guard():
    """Landlord only. Each landlord sees and edits only their own properties."""


def _coords(f):
    """Read the lat/lng pair from a form. (None, None) = not given. Raises ValueError if invalid."""
    la, ln = f.get("latitude", "").strip(), f.get("longitude", "").strip()
    if not la and not ln:
        return None, None
    lat, lng = float(la), float(ln)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError
    return lat, lng


@bp.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        name, addr = request.form.get("name", "").strip(), request.form.get("address", "").strip()
        if not name or not addr:
            flash("Name and address are required.", "danger")
            return redirect(url_for("properties.index"))
        try:
            lat, lng = _coords(request.form)
        except ValueError:
            flash("Latitude and longitude must both be valid numbers.", "danger")
            return redirect(url_for("properties.index"))
        p = Property(owner_id=ownership.me(), name=name, address=addr)
        p.set_location(lat, lng)
        db.session.add(p)
        db.session.commit()
        flash("Property added.", "success")
        return redirect(url_for("properties.detail", property_id=p.id))

    rows = db.session.execute(
        db.select(
            Property,
            db.func.count(Unit.id),
            db.func.count(Unit.id).filter(Unit.status == "occupied"),
            db.func.count(Unit.id).filter(Unit.status == "vacant"),
            db.func.coalesce(db.func.sum(Unit.monthly_rent).filter(Unit.status == "occupied"), 0),
        )
        .outerjoin(Unit, Unit.property_id == Property.id)
        .where(Property.owner_id == ownership.me())
        .group_by(Property.id).order_by(Property.name)
    ).all()
    return render_template("admin/properties.html", title="Properties & units", rows=rows, maps=True)


@bp.route("/<int:property_id>", methods=["GET", "POST"])
def detail(property_id):
    prop = ownership.own_property(property_id)
    if request.method == "POST":
        return _handle_post(prop)

    units = db.session.execute(
        db.select(Unit, User.name, Tenant.id)
        .outerjoin(Lease, db.and_(Lease.unit_id == Unit.id, Lease.status == "active"))
        .outerjoin(Tenant, Tenant.id == Lease.tenant_id).outerjoin(User, User.id == Tenant.user_id)
        .where(Unit.property_id == property_id).order_by(Unit.unit_no)
    ).all()

    nearby = []
    if prop.location is not None:  # PostGIS: other properties within N metres, nearest first
        ref = db.select(Property.location).where(Property.id == property_id).scalar_subquery()
        here = sa.cast(ref, Geography)
        there = sa.cast(Property.location, Geography)
        dist = sa.func.ST_Distance(there, here)
        nearby = db.session.execute(
            db.select(Property, dist.label("metres"))
            .where(Property.id != property_id, Property.owner_id == prop.owner_id, Property.location.is_not(None),
                   sa.func.ST_DWithin(there, here, current_app.config["NEARBY_RADIUS_M"]))
            .order_by(dist)
        ).all()
    return render_template("admin/property_detail.html", title=prop.name, p=prop, units=units, nearby=nearby,
                           radius_km=current_app.config["NEARBY_RADIUS_M"] / 1000, maps=True)


def _handle_post(prop):
    f = request.form
    action = f.get("action")
    try:
        if action == "save_property":
            name, addr = f.get("name", "").strip(), f.get("address", "").strip()
            if not name or not addr:
                flash("Name and address are required.", "danger")
            else:
                prop.name, prop.address = name, addr
                try:
                    lat, lng = _coords(f)
                except ValueError:
                    db.session.rollback()
                    flash("Latitude and longitude must both be valid numbers.", "danger")
                    return redirect(url_for("properties.detail", property_id=prop.id))
                if (lat, lng) != (prop.lat, prop.lng):
                    prop.set_location(lat, lng)
                db.session.commit()
                flash("Property saved.", "success")
        elif action == "delete_property":
            db.session.delete(prop)
            db.session.commit()
            flash("Property deleted.", "success")
            return redirect(url_for("properties.index"))
        elif action == "add_unit":
            no, rent = f.get("unit_no", "").strip(), to_float(f.get("monthly_rent"))
            if not no or rent <= 0:
                flash("Unit number and a rent above zero are required.", "danger")
            else:
                db.session.add(Unit(property_id=prop.id, unit_no=no, monthly_rent=rent))
                db.session.commit()
                flash(f"Unit {no} added.", "success")
        elif action == "update_unit":
            unit = db.session.get(Unit, int(to_float(f.get("unit_id")))) or abort(404)
            if unit.property_id != prop.id:
                abort(404)
            status, rent = f.get("status", ""), to_float(f.get("monthly_rent"))
            has_lease = db.session.scalar(
                db.select(db.func.count(Lease.id)).where(Lease.unit_id == unit.id, Lease.status == "active"))
            # Business rule: "occupied" is controlled by leases, not typed by hand.
            if status not in ("vacant", "occupied", "maintenance"):
                flash("Unknown status.", "danger")
            elif has_lease and status != "occupied":
                flash("This unit has an active lease. End the lease first.", "danger")
            elif not has_lease and status == "occupied":
                flash("Create a lease to mark a unit occupied.", "danger")
            elif rent <= 0:
                flash("Rent must be above zero.", "danger")
            else:
                unit.monthly_rent, unit.status = rent, status
                db.session.commit()
                flash("Unit updated. Rent changes apply to new leases only.", "success")
        elif action == "delete_unit":
            unit = db.session.get(Unit, int(to_float(f.get("unit_id")))) or abort(404)
            if unit.property_id != prop.id:
                abort(404)
            photos.delete_all(unit)
            db.session.delete(unit)
            db.session.commit()
            flash("Unit deleted.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Not allowed: that unit number already exists, or the record is still used by leases/units.", "danger")
    return redirect(url_for("properties.detail", property_id=prop.id))


# ---------------- unit photos ----------------
@bp.route("/<int:property_id>/units/<int:unit_id>/photos", methods=["GET", "POST"])
def unit_photos(property_id, unit_id):
    unit = ownership.own_unit(unit_id)
    if unit.property_id != property_id:
        abort(404)
    if request.method == "POST":
        f = request.form
        action = f.get("action")
        if action == "upload":
            files = [x for x in request.files.getlist("photos") if x and x.filename]
            if not files:
                flash("Choose at least one image.", "danger")
            saved, problems = 0, []
            for file in files:
                try:
                    photos.save(unit, file, f.get("caption"))
                    db.session.flush()
                    db.session.refresh(unit)
                    saved += 1
                except ValueError as ex:
                    problems.append(str(ex))
            db.session.commit()
            if saved:
                flash(f"{saved} photo(s) uploaded.", "success")
            for msg in problems:
                flash(msg, "danger")
        else:
            photo = db.session.get(UnitPhoto, int(to_float(f.get("photo_id")))) or abort(404)
            if photo.unit_id != unit.id:
                abort(404)
            if action == "delete":
                photos.delete(photo)
                flash("Photo deleted.", "success")
            elif action == "cover":
                photos.make_cover(photo)
                flash("Cover photo set.", "success")
            elif action == "caption":
                photo.caption = f.get("caption", "").strip()[:120] or None
                flash("Caption saved.", "success")
            else:
                abort(400)
            db.session.commit()
        return redirect(url_for("properties.unit_photos", property_id=property_id, unit_id=unit.id))
    return render_template("admin/unit_photos.html", title=f"Photos: {unit.property.name} {unit.unit_no}", u=unit,
                           max_photos=photos.MAX_PER_UNIT)


# ---------------- Excel import ----------------
@bp.route("/import", methods=["GET", "POST"])
def import_page():
    result = None
    if request.method == "POST":
        file = request.files.get("file")
        if not file or not file.filename.lower().endswith(".xlsx"):
            flash("Choose an .xlsx file.", "danger")
        else:
            result = excel.import_units(file.stream, ownership.me())
            if result.ok:
                flash(f"Import done: {result.properties_created} propert(ies) created, "
                      f"{result.units_created} unit(s) created, {result.units_updated} unit(s) updated.", "success")
    return render_template("admin/import.html", title="Import from Excel", result=result)


@bp.get("/import/template.xlsx")
def import_template():
    return send_file(BytesIO(excel.import_template()), as_attachment=True, download_name="smartrent-import-template.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
