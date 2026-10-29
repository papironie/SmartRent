"""Database tables. SQLAlchemy ORM on PostgreSQL + PostGIS.

Changes from the PHP/MySQL version:
  * No payments table and no v_charges view (payment function excluded).
  * properties.location is a PostGIS POINT (SRID 4326) for the Leaflet map.
  * "One active lease per unit / per tenant" is enforced by partial unique
    indexes in the database, not only by application code.

Multi-landlord platform. Roles:
  superadmin : runs the platform (landlord accounts, platform overview). Never manages properties.
  landlord   : owns properties (properties.owner_id) and the tenants they created (tenants.landlord_id).
               Units, leases, charges, photos, inquiries and views belong to a landlord through the property.
  tenant     : has a lease with one landlord.
  guest      : registered visitor who browses vacant units and sends inquiries.
"""
from datetime import datetime

import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.orm import relationship
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db

ROLES = ("superadmin", "landlord", "tenant", "guest")


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)  # hashed, never plain text
    role = db.Column(db.String(10), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=sa.true())
    last_login_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, server_default=sa.func.now())

    tenant = relationship("Tenant", back_populates="user", uselist=False, foreign_keys="Tenant.user_id")

    __table_args__ = (sa.CheckConstraint(f"role IN {ROLES}", name="ck_user_role"),)

    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)


class Property(db.Model):
    __tablename__ = "properties"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)  # the landlord
    name = db.Column(db.String(120), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    location = db.Column(Geometry("POINT", srid=4326, spatial_index=True))  # PostGIS
    elevation_m = db.Column(db.Float)  # cached from OpenTopoData (elevation does not change)
    created_at = db.Column(db.DateTime, nullable=False, server_default=sa.func.now())

    # Read-only coordinates computed by PostGIS, handy in templates and JSON.
    lat = db.column_property(sa.func.ST_Y(location))
    lng = db.column_property(sa.func.ST_X(location))

    units = relationship("Unit", back_populates="property", order_by="Unit.unit_no")
    owner = relationship("User")

    __table_args__ = (sa.Index("idx_property_owner", "owner_id"),)

    def set_location(self, lat: float | None, lng: float | None) -> None:
        if lat is None or lng is None:
            self.location = None
        else:
            self.location = sa.func.ST_SetSRID(sa.func.ST_MakePoint(lng, lat), 4326)
        self.elevation_m = None  # moved, so the cached elevation is stale


class Unit(db.Model):
    __tablename__ = "units"
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id", ondelete="RESTRICT"), nullable=False)
    unit_no = db.Column(db.String(20), nullable=False)
    monthly_rent = db.Column(db.Numeric(10, 2), nullable=False)  # NUMERIC, never float, for money
    status = db.Column(db.String(12), nullable=False, default="vacant")

    property = relationship("Property", back_populates="units")
    leases = relationship("Lease", back_populates="unit")
    photos = relationship("UnitPhoto", back_populates="unit", order_by="UnitPhoto.position, UnitPhoto.id",
                          cascade="all, delete-orphan", passive_deletes=True)  # photos[0] = cover

    __table_args__ = (
        sa.UniqueConstraint("property_id", "unit_no", name="uq_unit"),
        sa.CheckConstraint("status IN ('vacant','occupied','maintenance')", name="ck_unit_status"),
        sa.CheckConstraint("monthly_rent > 0", name="ck_unit_rent"),
    )


class Tenant(db.Model):
    __tablename__ = "tenants"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, unique=True)
    landlord_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    phone = db.Column(db.String(30), nullable=False)
    emergency_name = db.Column(db.String(100))
    emergency_phone = db.Column(db.String(30))
    created_at = db.Column(db.DateTime, nullable=False, server_default=sa.func.now())

    user = relationship("User", back_populates="tenant", foreign_keys=[user_id])
    leases = relationship("Lease", back_populates="tenant", order_by="Lease.start_date.desc()")

    __table_args__ = (sa.Index("idx_tenant_landlord", "landlord_id"),)


class Lease(db.Model):
    __tablename__ = "leases"
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey("units.id", ondelete="RESTRICT"), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    monthly_rent = db.Column(db.Numeric(10, 2), nullable=False)  # copied from unit at signing
    deposit = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    due_day = db.Column(db.SmallInteger, nullable=False)  # 1..31, capped to month length when billing
    status = db.Column(db.String(10), nullable=False, default="active")
    created_at = db.Column(db.DateTime, nullable=False, server_default=sa.func.now())

    tenant = relationship("Tenant", back_populates="leases")
    unit = relationship("Unit", back_populates="leases")
    charges = relationship("Charge", back_populates="lease")

    __table_args__ = (
        sa.CheckConstraint("end_date > start_date", name="ck_lease_dates"),
        sa.CheckConstraint("due_day BETWEEN 1 AND 31", name="ck_lease_due_day"),
        sa.CheckConstraint("status IN ('active','ended')", name="ck_lease_status"),
        sa.Index("idx_lease_status", "status", "end_date"),
        # The database itself refuses a second active lease on one unit or for one tenant.
        sa.Index("uq_active_lease_unit", "unit_id", unique=True, postgresql_where=sa.text("status = 'active'")),
        sa.Index("uq_active_lease_tenant", "tenant_id", unique=True, postgresql_where=sa.text("status = 'active'")),
    )


class Charge(db.Model):
    """One monthly rent statement per lease. UNIQUE(lease_id, period) makes billing safe to run twice."""

    __tablename__ = "charges"
    id = db.Column(db.Integer, primary_key=True)
    lease_id = db.Column(db.Integer, db.ForeignKey("leases.id", ondelete="RESTRICT"), nullable=False)
    period = db.Column(db.Date, nullable=False)  # always the 1st of the month
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, server_default=sa.func.now())

    lease = relationship("Lease", back_populates="charges")

    __table_args__ = (
        sa.UniqueConstraint("lease_id", "period", name="uq_charge"),
        sa.Index("idx_due", "due_date"),
    )


class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type = db.Column(db.String(40), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    dedupe_key = db.Column(db.String(120), unique=True)  # stops the daily job sending the same alert twice
    read_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, server_default=sa.func.now())

    __table_args__ = (sa.Index("idx_notif_user", "user_id", "read_at"),)


class UnitPhoto(db.Model):
    """A picture of a unit. The file lives in UPLOAD_DIR/units/<filename>; position 0 = cover photo."""

    __tablename__ = "unit_photos"
    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey("units.id", ondelete="CASCADE"), nullable=False)
    filename = db.Column(db.String(120), nullable=False)  # random name we chose, never the uploader's
    caption = db.Column(db.String(120))
    position = db.Column(db.SmallInteger, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, server_default=sa.func.now())

    unit = relationship("Unit", back_populates="photos")

    __table_args__ = (sa.Index("idx_photo_unit", "unit_id", "position"),)


class Inquiry(db.Model):
    """A guest asks about a vacant unit (and may propose a viewing date)."""

    __tablename__ = "inquiries"
    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey("units.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    phone = db.Column(db.String(30))
    message = db.Column(db.String(1000), nullable=False)
    viewing_date = db.Column(db.Date)
    status = db.Column(db.String(10), nullable=False, default="new")
    created_at = db.Column(db.DateTime, nullable=False, server_default=sa.func.now())

    unit = relationship("Unit")
    user = relationship("User")

    __table_args__ = (
        sa.CheckConstraint("status IN ('new','contacted','closed')", name="ck_inquiry_status"),
        sa.Index("idx_inquiry_status", "status", "created_at"),
    )


class UnitView(db.Model):
    """One row per listing page view. Feeds the demand analytics (views vs inquiries)."""

    __tablename__ = "unit_views"
    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey("units.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))  # NULL = anonymous visitor
    viewed_at = db.Column(db.DateTime, nullable=False, server_default=sa.func.now())

    __table_args__ = (sa.Index("idx_view_unit", "unit_id", "viewed_at"),)
