import os
from datetime import date

import psycopg2
import pytest
from sqlalchemy import text

from config import TestConfig
from smartrent import create_app
from smartrent.cli import init_database
from smartrent.extensions import db
from smartrent.models import Lease, Property, Tenant, Unit, User

FROZEN = date(2026, 9, 11)


def _ensure_test_database():
    """Create the smartrent_test database on the same server if it does not exist."""
    url = TestConfig.SQLALCHEMY_DATABASE_URI.replace("+psycopg2", "")
    base, name = url.rsplit("/", 1)
    conn = psycopg2.connect(base + "/postgres")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{name}"')
    conn.close()


@pytest.fixture(scope="session")
def app():
    _ensure_test_database()
    app = create_app(TestConfig)
    with app.app_context():
        db.session.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        db.session.commit()
        db.drop_all()
        init_database()
    return app


@pytest.fixture(autouse=True)
def _clean(app):
    """Fresh tables and a frozen date for every test."""
    app.config["TODAY"] = FROZEN
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        tables = ", ".join(t.name for t in reversed(db.metadata.sorted_tables) if t.name != "spatial_ref_sys")
        db.session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        db.session.commit()
    yield
    with app.app_context():
        db.session.remove()


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield


@pytest.fixture
def client(app):
    return app.test_client()


# ---------- factories (call inside `ctx`) ----------
@pytest.fixture
def make(ctx):
    class F:
        n = 0

        def landlord(self, email="admin@t.test", pw="admin123", name="Landlord"):
            """Get or create a landlord. The default one owns everything the other factories make."""
            u = db.session.scalar(db.select(User).where(User.email == email))
            if u is None:
                u = User(name=name, email=email, role="landlord")
                u.set_password(pw)
                db.session.add(u)
                db.session.commit()
            return u

        admin = landlord  # older tests call make.admin()

        def prop(self, name="Prop", lat=None, lng=None, address="1 Main St", owner=None):
            p = Property(owner_id=(owner or self.landlord()).id, name=name, address=address)
            if lat is not None:
                p.set_location(lat, lng)
            db.session.add(p)
            db.session.commit()
            return p

        def unit(self, prop, no="U-1", rent=5000, status="vacant"):
            u = Unit(property_id=prop.id, unit_no=no, monthly_rent=rent, status=status)
            db.session.add(u)
            db.session.commit()
            return u

        def tenant(self, name="Tenant", pw="tenant123", landlord=None):
            F.n += 1
            u = User(name=name, email=f"t{F.n}@t.test", role="tenant")
            u.set_password(pw)
            t = Tenant(user=u, landlord_id=(landlord or self.landlord()).id, phone="0917")
            db.session.add_all([u, t])
            db.session.commit()
            return t

        def lease(self, tenant, unit, start=date(2026, 1, 1), end=date(2026, 12, 31), rent=None, due_day=5, status="active"):
            l = Lease(tenant_id=tenant.id, unit_id=unit.id, start_date=start, end_date=end,
                      monthly_rent=rent or unit.monthly_rent, deposit=0, due_day=due_day, status=status)
            if status == "active":
                unit.status = "occupied"
            db.session.add(l)
            db.session.commit()
            return l

    return F()


def login(client, email, pw):
    return client.post("/login", data={"email": email, "password": pw})
