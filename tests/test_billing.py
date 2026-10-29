from datetime import date

from smartrent.extensions import db
from smartrent.models import Charge, Notification
from smartrent.services import billing


def _charges():
    return db.session.scalars(db.select(Charge).order_by(Charge.id)).all()


def test_generate_is_idempotent(make):
    p = make.prop(); t = make.tenant(); l = make.lease(t, make.unit(p))
    assert billing.generate(date(2026, 9, 11)) == 1
    assert billing.generate(date(2026, 9, 30)) == 0          # same month, second run
    assert len(_charges()) == 1
    c = _charges()[0]
    assert c.period == date(2026, 9, 1) and c.due_date == date(2026, 9, 5) and float(c.amount) == 5000.0


def test_due_day_capped_to_month_length(make):
    p = make.prop(); t = make.tenant(); make.lease(t, make.unit(p), due_day=31)
    billing.generate(date(2026, 9, 1))   # 30 days
    billing.generate(date(2026, 2, 1))   # 28 days
    due = {c.period: c.due_date for c in _charges()}
    assert due[date(2026, 9, 1)] == date(2026, 9, 30)
    assert due[date(2026, 2, 1)] == date(2026, 2, 28)


def test_only_overlapping_active_leases_are_billed(make):
    p = make.prop()
    a = make.lease(make.tenant("A"), make.unit(p, "1"), start=date(2026, 1, 1), end=date(2026, 12, 31))
    make.lease(make.tenant("B"), make.unit(p, "2"), start=date(2026, 10, 1), end=date(2027, 9, 30))     # starts later
    make.lease(make.tenant("C"), make.unit(p, "3"), start=date(2025, 1, 1), end=date(2025, 12, 31))     # already over
    make.lease(make.tenant("D"), make.unit(p, "4"), status="ended")                                        # ended
    assert billing.generate(date(2026, 9, 11)) == 1
    assert _charges()[0].lease_id == a.id


def test_lease_ending_mid_month_still_billed_that_month(make):
    p = make.prop()
    make.lease(make.tenant(), make.unit(p), start=date(2026, 1, 1), end=date(2026, 9, 2))
    assert billing.generate(date(2026, 9, 20)) == 1


def test_daily_tasks_reminders_and_lease_alerts_dedupe(make, app):
    p = make.prop()
    make.admin()
    t1 = make.tenant("Soon"); make.lease(t1, make.unit(p, "1"), end=date(2026, 9, 25), due_day=13)   # due in 2 days; ends in 14
    t2 = make.tenant("Later"); make.lease(t2, make.unit(p, "2"), due_day=28)                          # due in 17 days
    first = billing.daily_tasks()
    assert first["charges"] == 2 and first["reminders"] == 1 and first["leases"] == 1
    second = billing.daily_tasks()
    assert second == {"charges": 0, "reminders": 0, "leases": 0}
    types = sorted(n.type for n in db.session.scalars(db.select(Notification)))
    # tenant: new_charge x2, charge_reminder, lease_expiring ; admin: lease_expiring
    assert types.count("new_charge") == 2 and types.count("charge_reminder") == 1 and types.count("lease_expiring") == 2


def test_email_falls_back_to_log(make, app, tmp_path):
    app.config["MAIL_LOG"] = str(tmp_path / "mail.log")
    p = make.prop(); t = make.tenant(); make.lease(t, make.unit(p))
    billing.generate(date(2026, 9, 11))
    assert "new charge" in (tmp_path / "mail.log").read_text()
