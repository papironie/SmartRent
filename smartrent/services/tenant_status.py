"""Tenant status, computed live from the lease. Never stored.

Payments are out of scope, so status is about the lease, not payment behaviour:
  no_lease : tenant has no active lease
  expired  : active lease whose end date has passed (still occupying)
  ending   : lease ends within LEASE_WARN_DAYS
  new      : lease started within NEW_TENANT_DAYS
  active   : everything else
"""
from dateutil.relativedelta import relativedelta
from flask import current_app

from ..extensions import db
from ..models import Lease, Tenant
from ..utils import today

LABELS = {
    "expired": "Lease expired",
    "ending": "Ending soon",
    "new": "New",
    "active": "Active",
    "no_lease": "No lease",
}
BADGE = {  # Bootstrap contextual colours
    "expired": "danger", "ending": "warning", "new": "info", "active": "success", "no_lease": "secondary",
}


def classify(lease: Lease | None, now=None) -> str:
    now = now or today()
    cfg = current_app.config
    if lease is None or lease.status != "active":
        return "no_lease"
    if lease.end_date < now:
        return "expired"
    if lease.end_date <= now + relativedelta(days=cfg["LEASE_WARN_DAYS"]):
        return "ending"
    if lease.start_date >= now - relativedelta(days=cfg["NEW_TENANT_DAYS"]):
        return "new"
    return "active"


def active_lease_map(landlord_id: int | None = None) -> dict[int, Lease]:
    q = db.select(Lease).join(Tenant, Tenant.id == Lease.tenant_id).where(Lease.status == "active")
    if landlord_id is not None:
        q = q.where(Tenant.landlord_id == landlord_id)
    return {l.tenant_id: l for l in db.session.scalars(q)}


def all_statuses(landlord_id: int | None = None) -> dict[int, str]:
    """Status of every tenant, or only one landlord's tenants."""
    leases = active_lease_map(landlord_id)
    q = db.select(Tenant.id)
    if landlord_id is not None:
        q = q.where(Tenant.landlord_id == landlord_id)
    return {tid: classify(leases.get(tid)) for tid in db.session.scalars(q)}
