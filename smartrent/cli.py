"""Command line tools:  flask init-db | seed | reset-db | daily"""
import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import text

from .extensions import db
from .models import ROLES
from .services import billing, seed as seed_service


# Changes to tables that already existed before create_all() knew about them. Safe to run twice.
# Existing data is handed to the first landlord (the old single "admin" becomes that landlord).
UPGRADES = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE users ALTER COLUMN role TYPE VARCHAR(10)",
    "ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_user_role",
    "UPDATE users SET role = 'landlord' WHERE role = 'admin'",
    f"ALTER TABLE users ADD CONSTRAINT ck_user_role CHECK (role IN {ROLES})",
    "ALTER TABLE properties ADD COLUMN IF NOT EXISTS owner_id INTEGER REFERENCES users(id) ON DELETE RESTRICT",
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS landlord_id INTEGER REFERENCES users(id) ON DELETE RESTRICT",
    "UPDATE properties SET owner_id = (SELECT min(id) FROM users WHERE role = 'landlord') WHERE owner_id IS NULL",
    """UPDATE tenants t SET landlord_id = COALESCE(
         (SELECT p.owner_id FROM leases l JOIN units u ON u.id = l.unit_id JOIN properties p ON p.id = u.property_id
          WHERE l.tenant_id = t.id ORDER BY l.start_date DESC LIMIT 1),
         (SELECT min(id) FROM users WHERE role = 'landlord'))
       WHERE landlord_id IS NULL""",
    "CREATE INDEX IF NOT EXISTS idx_property_owner ON properties (owner_id)",
    "CREATE INDEX IF NOT EXISTS idx_tenant_landlord ON tenants (landlord_id)",
]


def init_database() -> None:
    db.session.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    db.session.commit()
    db.create_all()
    for sql in UPGRADES:
        db.session.execute(text(sql))
    for table, col in (("properties", "owner_id"), ("tenants", "landlord_id")):
        # NOT NULL only once every row has an owner (an old database with no landlord yet keeps NULLs).
        if not db.session.scalar(text(f"SELECT count(*) FROM {table} WHERE {col} IS NULL")):
            db.session.execute(text(f"ALTER TABLE {table} ALTER COLUMN {col} SET NOT NULL"))
    db.session.commit()


@click.command("init-db")
@with_appcontext
def init_db_cmd():
    """Enable PostGIS and create all tables (safe to run twice)."""
    init_database()
    click.echo("PostGIS enabled, tables ready.")


@click.command("reset-db")
@click.confirmation_option(prompt="This DELETES every table. Continue?")
@with_appcontext
def reset_db_cmd():
    """Drop and recreate all tables."""
    db.session.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    db.session.commit()
    db.drop_all()
    init_database()
    click.echo("Database reset.")


@click.command("seed")
@with_appcontext
def seed_cmd():
    """Load demo data (WIPES all data first)."""
    r = seed_service.run()
    click.echo(f"Seeded: {r}")
    click.echo("Superadmin:   superadmin@smartrent.test / super123")
    click.echo("Landlords:    landlord@smartrent.test / landlord123, landlord2@smartrent.test / landlord123")
    click.echo("Tenant login: juan.dela.cruz@mail.test / tenant123 (all tenants use tenant123)")
    click.echo("Guest login:  guest@smartrent.test / guest123")


@click.command("daily")
@with_appcontext
def daily_cmd():
    """Daily job: month's charges, reminders, lease-ending alerts. Safe to run twice."""
    r = billing.daily_tasks()
    click.echo(f"charges:{r['charges']} reminders:{r['reminders']} leases:{r['leases']}")


def register(app):
    for cmd in (init_db_cmd, reset_db_cmd, seed_cmd, daily_cmd):
        app.cli.add_command(cmd)
