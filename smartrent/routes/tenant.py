"""Tenant portal. A tenant can only ever see their own rows (filtered by the session's tenant_id)."""
from datetime import datetime

from flask import Blueprint, render_template, session

from ..auth_helpers import role_required
from ..extensions import db
from ..models import Charge, Lease, Notification, Property, Unit
from ..services import tenant_status
from ..utils import today

bp = Blueprint("tenant", __name__, url_prefix="/tenant")


@bp.before_request
@role_required("tenant")
def _guard():
    """Tenant only."""


@bp.get("/")
def dashboard():
    me = session["user"]
    tid = me["tenant_id"]
    lease = db.session.execute(
        db.select(Lease, Unit.unit_no, Property.name, Property.address)
        .join(Unit, Unit.id == Lease.unit_id).join(Property, Property.id == Unit.property_id)
        .where(Lease.tenant_id == tid, Lease.status == "active")
    ).first()
    charges = db.session.scalars(
        db.select(Charge).join(Lease, Lease.id == Charge.lease_id)
        .where(Lease.tenant_id == tid).order_by(Charge.period.desc()).limit(12)
    ).all()
    next_charge = db.session.scalar(
        db.select(Charge).join(Lease, Lease.id == Charge.lease_id)
        .where(Lease.tenant_id == tid, Charge.due_date >= today()).order_by(Charge.due_date).limit(1)
    )
    notes = db.session.scalars(
        db.select(Notification).where(Notification.user_id == me["id"]).order_by(Notification.id.desc()).limit(8)
    ).all()
    unread_ids = [n.id for n in notes if n.read_at is None]
    status = tenant_status.classify(lease[0] if lease else None)
    html = render_template("tenant/dashboard.html", title="My rental account", lease=lease, charges=charges,
                           next_charge=next_charge, notes=notes, unread=set(unread_ids), status=status,
                           labels=tenant_status.LABELS, badge=tenant_status.BADGE)
    if unread_ids:  # mark read after rendering so this view still shows them as new
        db.session.execute(db.update(Notification).where(Notification.id.in_(unread_ids)).values(read_at=datetime.now()))
        db.session.commit()
    return html
