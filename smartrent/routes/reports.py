"""Reports as CSV (pandas) or native Excel (openpyxl)."""
import pandas as pd
from flask import Blueprint, Response, abort, render_template, request

from .. import ownership
from ..auth_helpers import role_required
from ..extensions import db
from ..models import Charge, Lease, Property, Tenant, Unit, User
from ..services import analytics, excel, tenant_status
from ..utils import last_n_periods, month_start, parse_month, today

bp = Blueprint("reports", __name__, url_prefix="/admin/reports")

REPORTS = {
    "tenants": "Tenant list with lease and status",
    "leases": "All leases",
    "rent_roll": "Rent roll: every unit with its tenant and rent",
    "charges": "Charges for one month",
    "unit_performance": "Unit performance: rent vs property median, tenure, idle months",
}
UNIT_COLUMNS = ["Property", "Unit", "Status", "Asking rent", "Property median", "vs median %",
                "Tenant", "Tenure (months)", "Last billed", "Idle months"]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@bp.before_request
@role_required("landlord")
def _guard():
    """Landlord only. Every report contains only the signed-in landlord's rows."""


@bp.get("/")
def index():
    return render_template("admin/reports.html", title="Reports", reports=REPORTS, month=f"{month_start(today()):%Y-%m}")


def _frame(kind: str, month) -> pd.DataFrame:
    me = ownership.me()
    if kind == "tenants":
        statuses = tenant_status.all_statuses(me)
        rows = db.session.execute(
            db.select(Tenant.id, User.name, User.email, Tenant.phone, Property.name, Unit.unit_no, Lease.end_date)
            .join(User, User.id == Tenant.user_id)
            .outerjoin(Lease, db.and_(Lease.tenant_id == Tenant.id, Lease.status == "active"))
            .outerjoin(Unit, Unit.id == Lease.unit_id).outerjoin(Property, Property.id == Unit.property_id)
            .where(Tenant.landlord_id == me)
            .order_by(User.name)
        ).all()
        return pd.DataFrame(
            [[n, e, ph, f"{p} {u}" if p else "", tenant_status.LABELS[statuses[tid]], end]
             for tid, n, e, ph, p, u, end in rows],
            columns=["Tenant", "Email", "Phone", "Unit", "Status", "Lease ends"])
    if kind == "leases":
        rows = db.session.execute(
            db.select(User.name, Property.name, Unit.unit_no, Lease.start_date, Lease.end_date, Lease.monthly_rent,
                      Lease.deposit, Lease.due_day, Lease.status)
            .join(Tenant, Tenant.id == Lease.tenant_id).join(User, User.id == Tenant.user_id)
            .join(Unit, Unit.id == Lease.unit_id).join(Property, Property.id == Unit.property_id)
            .where(Property.owner_id == me)
            .order_by(Lease.start_date.desc())
        ).all()
        return pd.DataFrame([[r[0], f"{r[1]} {r[2]}", r[3], r[4], float(r[5]), float(r[6]), r[7], r[8]] for r in rows],
                            columns=["Tenant", "Unit", "Start", "End", "Monthly rent", "Deposit", "Due day", "Status"])
    if kind == "rent_roll":
        rows = db.session.execute(
            db.select(Property.name, Unit.unit_no, Unit.status, Unit.monthly_rent, User.name)
            .join(Unit, Unit.property_id == Property.id)
            .outerjoin(Lease, db.and_(Lease.unit_id == Unit.id, Lease.status == "active"))
            .outerjoin(Tenant, Tenant.id == Lease.tenant_id).outerjoin(User, User.id == Tenant.user_id)
            .where(Property.owner_id == me)
            .order_by(Property.name, Unit.unit_no)
        ).all()
        return pd.DataFrame([[p, u, s, float(r), t or ""] for p, u, s, r, t in rows],
                            columns=["Property", "Unit", "Status", "Monthly rent", "Tenant"])
    if kind == "charges":
        rows = db.session.execute(
            db.select(User.name, Property.name, Unit.unit_no, Charge.period, Charge.due_date, Charge.amount)
            .join(Lease, Lease.id == Charge.lease_id).join(Tenant, Tenant.id == Lease.tenant_id)
            .join(User, User.id == Tenant.user_id).join(Unit, Unit.id == Lease.unit_id)
            .join(Property, Property.id == Unit.property_id)
            .where(Property.owner_id == me, Charge.period == month).order_by(User.name)
        ).all()
        return pd.DataFrame([[n, f"{p} {u}", per, due, float(a)] for n, p, u, per, due, a in rows],
                            columns=["Tenant", "Unit", "Month", "Due date", "Amount"])
    if kind == "unit_performance":
        df = analytics.unit_performance(ownership.property_ids())
        df.columns = UNIT_COLUMNS
        return df
    abort(404)


@bp.get("/analytics.xlsx")
def analytics_pack():
    """Every analytics table in one workbook, one sheet each. ?property=<id> narrows it."""
    pid, scope = ownership.scope(request.args.get("property"))
    k = analytics.kpis(scope)
    m = analytics.monthly(scope=scope)
    f = analytics.forecast(scope=scope)
    e = analytics.lease_expiry(scope=scope)
    t = analytics.tenure(scope)
    perf = analytics.unit_performance(scope)
    label = db.session.get(Property, pid).name if pid else "All my properties"
    summary = [
        ["Scope", label], ["As of", today()],
        ["Units", k["units_total"]], ["Occupied", k["occupied"]], ["Vacant", k["vacant"]],
        ["Maintenance", k["maintenance"]], ["Occupancy %", k["occupancy_rate"]],
        ["Economic occupancy %", k["economic_occupancy"]], ["Active leases", k["active_tenants"]],
        ["Rent roll / month", k["rent_roll"]], ["Average rent", k["avg_rent"]],
        ["Vacancy loss / month", k["vacancy_loss"]], ["Billed this month", k["billed_this_month"]],
        ["Billed last month", k["billed_last_month"]], ["Billed change %", k["billed_change_pct"]],
        ["Rent at risk (90 days)", k["at_risk_rent"]], ["Leases ending (90 days)", k["at_risk_leases"]],
        ["Average tenure (months)", k["avg_tenure_months"]],
    ]
    periods = [p.strftime("%Y-%m") for p in last_n_periods(len(m["labels"]))]
    stats = analytics.rent_stats(perf)
    perf.columns = UNIT_COLUMNS
    sheets = [
        ("Summary", ["Metric", "Value"], summary),
        ("Monthly", ["Month", "Billed", "Charges", "Units billed", "Occupancy %"],
         list(zip(periods, m["billed"], m["charges"], m["occupied_units"], m["occupancy"]))),
        ("Forecast", ["Month", "Contracted", "At risk (not renewed)", "Vacant potential"],
         list(zip(f["labels"], f["contracted"], f["at_risk"], f["vacant"]))),
        ("Lease expiry", ["Month", "Leases ending"], list(zip(e["labels"], e["values"]))),
        ("Tenure", ["Tenure", "Active leases"], list(zip(t["labels"], t["values"]))),
        ("Rent by property", ["Property", "Units", "Min", "Median", "Mean", "Max", "Occupied avg", "Vacant avg"],
         [[r["property"], r["units"], r["min"], r["median"], r["mean"], r["max"], r["occupied_avg"], r["vacant_avg"]] for r in stats]),
        ("Units", UNIT_COLUMNS, perf.astype(object).where(perf.notna(), None).values.tolist()),
        ("Demand", ["Property", "Unit", "Asking rent", "Photos", "Views (30 days)", "Inquiries (30 days)", "Inquiry rate %"],
         [[r["property"], r["unit_no"], r["rent"], r["photos"], r["views"], r["inquiries"], r["rate"]]
          for r in analytics.demand(scope)["rows"]]),
        ("Insights", ["Level", "Insight"], analytics.insights(scope)),
    ]
    name = f"smartrent-analytics-{today()}.xlsx"
    return Response(excel.build_multi_workbook(sheets), mimetype=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@bp.get("/<kind>.<fmt>")
def download(kind, fmt):
    if kind not in REPORTS or fmt not in ("csv", "xlsx"):
        abort(404)
    month = parse_month(request.args.get("month")) or month_start(today())
    df = _frame(kind, month)
    name = f"smartrent-{kind}-{today()}.{fmt}"
    if fmt == "csv":
        body = "﻿" + df.to_csv(index=False)  # BOM so Excel shows ₱ and ñ correctly
        return Response(body, mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})
    data = excel.build_workbook(kind.replace("_", " ").title(), list(df.columns), df.astype(object).where(df.notna(), None).values.tolist())
    return Response(data, mimetype=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"'})
