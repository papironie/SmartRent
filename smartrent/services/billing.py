"""Monthly billing + the daily job.

Rules (put these in your Scope & Limitations):
  * One charge per active lease per month, full month rent, no proration.
  * due_day 31 in a 30-day month becomes the 30th (capped to month length).
  * A lease counts if it overlaps the month at all.
"""
from dateutil.relativedelta import relativedelta
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..extensions import db
from ..models import Charge, Lease, Property, Tenant, Unit, User
from ..utils import fmt_date, money, month_end, month_label, month_start, today
from . import notify
from flask import current_app


def _owned(query, owner_id: int | None):
    """Limit a query that joins Lease to one landlord's properties. None = every landlord (scheduler)."""
    if owner_id is None:
        return query
    return query.where(Lease.unit_id.in_(
        db.select(Unit.id).join(Property, Property.id == Unit.property_id).where(Property.owner_id == owner_id)))


def generate(any_date, send_notifications: bool = True, owner_id: int | None = None) -> int:
    """Create missing charges for the month of any_date. Safe to run many times."""
    period = month_start(any_date)
    last_day = month_end(period)
    leases = db.session.execute(_owned(
        db.select(Lease, Tenant.user_id)
        .join(Tenant, Tenant.id == Lease.tenant_id)
        .where(Lease.status == "active", Lease.start_date <= last_day, Lease.end_date >= period), owner_id)
    ).all()

    created = 0
    for lease, user_id in leases:
        due = period.replace(day=min(lease.due_day, last_day.day))
        stmt = (
            pg_insert(Charge)
            .values(lease_id=lease.id, period=period, amount=lease.monthly_rent, due_date=due)
            .on_conflict_do_nothing(constraint="uq_charge")  # UNIQUE(lease_id, period)
            .returning(Charge.id)
        )
        if db.session.execute(stmt).first() is not None:
            created += 1
            if send_notifications:
                notify.create(
                    user_id,
                    "new_charge",
                    f"Your rent of {money(lease.monthly_rent)} for {month_label(period)} is due on {fmt_date(due)}.",
                    f"charge:{lease.id}:{period}",
                )
    db.session.commit()
    return created


def daily_tasks(owner_id: int | None = None) -> dict:
    """Run once a day (flask daily = every landlord, or a landlord's 'Run daily tasks now' = theirs). Idempotent."""
    cfg = current_app.config
    now = today()
    out = {"charges": generate(now, owner_id=owner_id), "reminders": 0, "leases": 0}

    # 1. Reminders: charges falling due within N days.
    soon = db.session.execute(_owned(
        db.select(Charge, Tenant.user_id)
        .join(Lease, Lease.id == Charge.lease_id)
        .join(Tenant, Tenant.id == Lease.tenant_id)
        .where(Charge.due_date.between(now, now + relativedelta(days=cfg["REMINDER_DAYS"]))), owner_id)
    ).all()
    for charge, user_id in soon:
        out["reminders"] += notify.create(
            user_id,
            "charge_reminder",
            f"Reminder: rent of {money(charge.amount)} for {month_label(charge.period)} is due on {fmt_date(charge.due_date)}.",
            f"remind:{charge.id}",
        )

    # 2. Leases ending soon.
    ending = db.session.execute(_owned(
        db.select(Lease, Tenant.user_id, User.name, Property.owner_id)
        .join(Tenant, Tenant.id == Lease.tenant_id)
        .join(User, User.id == Tenant.user_id)
        .join(Unit, Unit.id == Lease.unit_id).join(Property, Property.id == Unit.property_id)
        .where(Lease.status == "active", Lease.end_date.between(now, now + relativedelta(days=cfg["LEASE_WARN_DAYS"]))),
        owner_id)
    ).all()
    for lease, user_id, name, landlord_id in ending:
        if notify.create(
            user_id, "lease_expiring",
            f"Your lease ends on {fmt_date(lease.end_date)}. Talk to your landlord about renewal.",
            f"lease_end:{lease.id}",
        ):
            out["leases"] += 1
            notify.create(landlord_id, "lease_expiring", f"{name}'s lease ends {fmt_date(lease.end_date)}.",
                          f"lease_end:{lease.id}:owner")
    db.session.commit()
    return out
