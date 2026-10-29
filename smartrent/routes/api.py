"""JSON endpoints. The browser (Chart.js / Leaflet) calls these with fetch().
This is an architecture box: UI -> API -> database -> (external APIs)."""
from datetime import datetime

import sqlalchemy as sa
from flask import Blueprint, abort, jsonify, request

from .. import ownership
from ..auth_helpers import role_required
from ..extensions import db
from ..models import Property, Unit
from ..services import analytics, geo
from ..utils import today

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.before_request
@role_required("landlord")
def _guard():
    """Landlord only, and only their own data. Signed-out callers get redirected; wrong role gets 403."""


@bp.get("/analytics")
def analytics_json():
    """The landlord's own properties; ?property=<id> narrows to one (by_property always compares all of theirs)."""
    pid, scope = ownership.scope(request.args.get("property"))
    return jsonify({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "as_of": today().isoformat(),
        "property_id": pid,
        "kpis": analytics.kpis(scope),
        "monthly": analytics.monthly(scope=scope),
        "by_property": analytics.by_property(ownership.property_ids()),
        "occupancy": analytics.occupancy(scope),
        "lease_expiry": analytics.lease_expiry(scope=scope),
        "forecast": analytics.forecast(scope=scope),
        "tenure": analytics.tenure(scope),
        "demand": analytics.demand(scope)["monthly"],
    })


@bp.get("/properties.geojson")
def properties_geojson():
    """PostGIS builds the geometry; we only wrap it in a FeatureCollection."""
    rows = db.session.execute(
        db.select(
            Property.id, Property.name, Property.address,
            sa.func.ST_AsGeoJSON(Property.location).label("geom"),
            db.func.count(Unit.id).label("units"),
            db.func.count(Unit.id).filter(Unit.status == "vacant").label("vacant"),
        )
        .outerjoin(Unit, Unit.property_id == Property.id)
        .where(Property.owner_id == ownership.me(), Property.location.is_not(None)).group_by(Property.id)
    ).all()
    import json

    return jsonify({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature", "geometry": json.loads(r.geom),
            "properties": {"id": r.id, "name": r.name, "address": r.address, "units": r.units, "vacant": r.vacant},
        } for r in rows],
    })


@bp.get("/properties/<int:property_id>/site")
def property_site(property_id):
    """Elevation (OpenTopoData, cached in our DB) + weather (Open-Meteo)."""
    prop = ownership.own_property(property_id)
    if prop.lat is None:
        return jsonify({"error": "This property has no map location yet."}), 422
    elevation = prop.elevation_m
    if elevation is None:
        elevation = geo.fetch_elevation(prop.lat, prop.lng)
        if elevation is not None:
            prop.elevation_m = elevation
            db.session.commit()
    weather = geo.fetch_weather(prop.lat, prop.lng)
    return jsonify({
        "elevation_m": elevation,
        "weather": weather,
        "warnings": [w for w, bad in (("Elevation service unavailable.", elevation is None),
                                      ("Weather service unavailable.", weather is None)) if bad],
    })
