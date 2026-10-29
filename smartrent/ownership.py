"""Data isolation between landlords. Every landlord page loads records through these helpers.

A record that belongs to another landlord is answered with 404, not 403, so its existence is not revealed.
Ownership chain: Property.owner_id -> Unit -> Lease -> Charge (and Unit -> photos, inquiries, views);
Tenant.landlord_id for tenants (a tenant may exist before they have a lease).
"""
from flask import abort, session

from .extensions import db
from .models import Lease, Property, Tenant, Unit


def me() -> int:
    """The signed-in landlord's user id. Only call behind role_required("landlord")."""
    return session["user"]["id"]


def property_ids(owner_id: int | None = None) -> list[int]:
    owner_id = me() if owner_id is None else owner_id
    return list(db.session.scalars(db.select(Property.id).where(Property.owner_id == owner_id).order_by(Property.id)))


def scope(raw=None) -> tuple[int | None, list[int]]:
    """(selected property id or None, property ids to analyse) for ?property=<id>. Foreign ids are ignored."""
    ids = property_ids()
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        return None, ids
    return (pid, [pid]) if pid in ids else (None, ids)


def own_property(property_id) -> Property:
    p = db.session.get(Property, property_id)
    if p is None or p.owner_id != me():
        abort(404)
    return p


def own_unit(unit_id) -> Unit:
    u = db.session.get(Unit, unit_id)
    if u is None or u.property.owner_id != me():
        abort(404)
    return u


def own_tenant(tenant_id) -> Tenant:
    t = db.session.get(Tenant, tenant_id)
    if t is None or t.landlord_id != me():
        abort(404)
    return t


def own_lease(lease_id) -> Lease:
    lease = db.session.get(Lease, lease_id)
    if lease is None or lease.unit.property.owner_id != me():
        abort(404)
    return lease
