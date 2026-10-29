"""Analytics with pandas.

Metric definitions (write these in your paper, the panel WILL ask):
  Billed              = sum of charges whose period is that month
  Rent roll           = sum of monthly rent on ACTIVE leases (what you expect to bill per month)
  Occupancy rate      = occupied units / all units x 100 (maintenance counts as not occupied)
  Economic occupancy  = rent roll / (rent roll + asking rent of vacant and maintenance units) x 100
                        (how much of the possible rent is actually under lease)
  Vacancy loss        = sum of monthly rent of VACANT units (rent not earned every month)
  Occupancy trend     = units that received a charge in a month / all units today x 100
  Rent at risk (90d)  = monthly rent of active leases that end within 90 days (or already ended)
  Contracted rent     = rent of active leases that cover a future month (same rule billing uses)
  Tenure              = months since an active lease started
  Idle months         = months since a not-occupied unit was last billed
  Listing views       = visits to a unit's public page by visitors, guests and tenants (staff not counted)
  Inquiry rate        = inquiries / listing views x 100, over the last 30 days

Every function takes a scope: None = the whole platform, an int = one property,
a list = those properties (a landlord passes their own property ids, see ownership.scope()).

No payment data exists in this version, so there is no collection rate,
on-time rate or arrears. The old PHP version had them; they need payments.
"""
from datetime import date, datetime, time

import pandas as pd
from dateutil.relativedelta import relativedelta
from flask import current_app

from ..extensions import db
from ..models import Charge, Inquiry, Lease, Property, Tenant, Unit, UnitPhoto, UnitView, User
from ..utils import last_n_periods, money, month_end, month_start, today

DAYS_PER_MONTH = 30.4375
RISK_DAYS = 90
TENURE_BINS = [0, 6, 12, 24, float("inf")]
TENURE_LABELS = ["Under 6 months", "6–12 months", "1–2 years", "2+ years"]
Scope = int | list[int] | None


def _df(query) -> pd.DataFrame:
    """Run a SQLAlchemy select and return a DataFrame."""
    return pd.read_sql_query(query, db.session.connection())


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0.0).astype(float)


def _ids(scope: Scope) -> list[int] | None:
    return None if scope is None else ([scope] if isinstance(scope, int) else list(scope))


def _scoped(query, scope: Scope):
    """Limit a query that already has Unit in its FROM to the scope's properties."""
    ids = _ids(scope)
    return query if ids is None else query.where(Unit.property_id.in_(ids))


def _months_between(later: date, earlier: date) -> int:
    return (later.year - earlier.year) * 12 + later.month - earlier.month


def _active_leases(scope: Scope) -> pd.DataFrame:
    d = _df(_scoped(
        db.select(Lease.monthly_rent, Lease.start_date, Lease.end_date)
        .join(Unit, Unit.id == Lease.unit_id).where(Lease.status == "active"),
        scope,
    ))
    d["monthly_rent"] = _num(d["monthly_rent"])
    return d


def _units(scope: Scope) -> pd.DataFrame:
    d = _df(_scoped(db.select(Unit.status, Unit.monthly_rent), scope))
    d["monthly_rent"] = _num(d["monthly_rent"])
    return d


def _billed(period: date, scope: Scope) -> float:
    q = (db.select(db.func.coalesce(db.func.sum(Charge.amount), 0)).select_from(Charge)
         .join(Lease, Lease.id == Charge.lease_id).join(Unit, Unit.id == Lease.unit_id)
         .where(Charge.period == period))
    return float(db.session.scalar(_scoped(q, scope)))


def records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> list of dicts with None instead of NaN (for Jinja and JSON)."""
    return df.astype(object).where(df.notna(), None).to_dict("records")


def kpis(scope: Scope = None) -> dict:
    units = _units(scope)
    count = units["status"].value_counts()
    total = int(len(units))
    occupied = int(count.get("occupied", 0))
    vacant = int(count.get("vacant", 0))

    leases = _active_leases(scope)
    now = today()
    warn = now + relativedelta(days=current_app.config["LEASE_WARN_DAYS"])
    risk = now + relativedelta(days=RISK_DAYS)
    ends = list(leases["end_date"])
    tenure = [(now - s).days / DAYS_PER_MONTH for s in leases["start_date"]]

    period = month_start(now)
    billed = _billed(period, scope)
    billed_prev = _billed(period - relativedelta(months=1), scope)

    rent_roll = float(leases["monthly_rent"].sum())
    vacancy_loss = float(units.loc[units["status"] == "vacant", "monthly_rent"].sum())
    idle_rent = float(units.loc[units["status"] == "maintenance", "monthly_rent"].sum())
    potential = rent_roll + vacancy_loss + idle_rent

    return {
        "units_total": total,
        "occupied": occupied,
        "vacant": vacant,
        "maintenance": int(count.get("maintenance", 0)),
        "occupancy_rate": round(occupied / total * 100, 1) if total else 0.0,
        "economic_occupancy": round(rent_roll / potential * 100, 1) if potential else 0.0,
        "active_tenants": int(len(leases)),
        "rent_roll": rent_roll,
        "avg_rent": round(rent_roll / len(leases), 2) if len(leases) else 0.0,
        "vacancy_loss": vacancy_loss,
        "billed_this_month": billed,
        "billed_last_month": billed_prev,
        "billed_change_pct": round((billed / billed_prev - 1) * 100, 1) if billed_prev else None,
        "leases_ending": sum(now <= e <= warn for e in ends),
        "leases_expired": sum(e < now for e in ends),
        "at_risk_rent": float(sum(r for r, e in zip(leases["monthly_rent"], ends) if e <= risk)),
        "at_risk_leases": sum(e <= risk for e in ends),
        "avg_tenure_months": round(sum(tenure) / len(tenure), 1) if tenure else 0.0,
    }


def monthly(months: int = 12, scope: Scope = None) -> dict:
    """Billed amount, charge count and occupancy (units billed / all units) per month."""
    periods = last_n_periods(months)
    d = _df(_scoped(
        db.select(Charge.period, Charge.amount, Lease.unit_id)
        .join(Lease, Lease.id == Charge.lease_id).join(Unit, Unit.id == Lease.unit_id)
        .where(Charge.period >= periods[0]),
        scope,
    ))
    d["amount"] = _num(d["amount"])
    d["period"] = pd.to_datetime(d["period"]).dt.date
    g = (d.groupby("period")
         .agg(billed=("amount", "sum"), charges=("amount", "size"), units=("unit_id", "nunique"))
         .reindex(periods, fill_value=0))
    total = len(_units(scope))
    return {
        "labels": [p.strftime("%b") for p in periods],
        "billed": [float(x) for x in g["billed"]],
        "charges": [int(x) for x in g["charges"]],
        "occupied_units": [int(x) for x in g["units"]],
        "occupancy": [round(int(x) / total * 100, 1) if total else 0.0 for x in g["units"]],
    }


def by_property(scope: Scope = None) -> dict:
    ids = _ids(scope)
    pq = db.select(Property.id, Property.name).order_by(Property.name)
    props = _df(pq if ids is None else pq.where(Property.id.in_(ids)))
    if props.empty:
        return {"labels": [], "rent_roll": [], "vacancy_loss": [], "occupancy": []}
    units = _df(_scoped(db.select(Unit.property_id, Unit.status, Unit.monthly_rent), scope))
    units["monthly_rent"] = _num(units["monthly_rent"])
    roll = _df(_scoped(
        db.select(Unit.property_id, Lease.monthly_rent)
        .join(Lease, Lease.unit_id == Unit.id)
        .where(Lease.status == "active"), scope)
    )
    roll["monthly_rent"] = _num(roll["monthly_rent"])

    props["rent_roll"] = props["id"].map(roll.groupby("property_id")["monthly_rent"].sum()).fillna(0.0)
    props["vacancy_loss"] = props["id"].map(
        units[units["status"] == "vacant"].groupby("property_id")["monthly_rent"].sum()
    ).fillna(0.0)
    tot = units.groupby("property_id").size()
    occ = units[units["status"] == "occupied"].groupby("property_id").size()
    props["occupancy"] = (props["id"].map(occ).fillna(0) / props["id"].map(tot).replace(0, pd.NA) * 100).fillna(0).round(1)
    props = props.sort_values("rent_roll", ascending=False)
    return {
        "labels": props["name"].tolist(),
        "rent_roll": props["rent_roll"].astype(float).tolist(),
        "vacancy_loss": props["vacancy_loss"].astype(float).tolist(),
        "occupancy": props["occupancy"].astype(float).tolist(),
    }


def occupancy(scope: Scope = None) -> dict:
    k = kpis(scope)
    return {"labels": ["Occupied", "Vacant", "Maintenance"], "values": [k["occupied"], k["vacant"], k["maintenance"]]}


def lease_expiry(months: int = 6, scope: Scope = None) -> dict:
    """How many active leases end in each of the next N months (expired ones go in the first bucket)."""
    start = month_start(today())
    buckets = [start + relativedelta(months=i) for i in range(months)]
    d = _active_leases(scope)
    if d.empty:
        counts = [0] * months
    else:
        ends = pd.to_datetime(d["end_date"]).dt.to_period("M").dt.to_timestamp()
        ends = ends.clip(lower=pd.Timestamp(start))  # leases already past their end land in this month
        c = ends.dt.date.value_counts()
        counts = [int(c.get(b, 0)) for b in buckets]
    return {"labels": [b.strftime("%b %Y") for b in buckets], "values": counts}


def forecast(months: int = 6, scope: Scope = None) -> dict:
    """For each coming month: rent already under contract, rent lost unless leases are renewed,
    and asking rent of vacant units (what filling them would add)."""
    start = month_start(today())
    buckets = [start + relativedelta(months=i) for i in range(months)]
    leases = _active_leases(scope)
    units = _units(scope)
    vacant = float(units.loc[units["status"] == "vacant", "monthly_rent"].sum())
    contracted, at_risk = [], []
    for b in buckets:
        live = (leases["start_date"] <= month_end(b)) & (leases["end_date"] >= b)
        lapsed = leases["end_date"] < b
        contracted.append(float(leases.loc[live, "monthly_rent"].sum()))
        at_risk.append(float(leases.loc[lapsed, "monthly_rent"].sum()))
    return {
        "labels": [b.strftime("%b %Y") for b in buckets],
        "contracted": contracted,
        "at_risk": at_risk,
        "vacant": [vacant] * months,
    }


def tenure(scope: Scope = None) -> dict:
    """Active leases grouped by how long the tenant has been there."""
    now = today()
    leases = _active_leases(scope)
    months = pd.Series([(now - s).days / DAYS_PER_MONTH for s in leases["start_date"]], dtype=float)
    c = pd.cut(months, bins=TENURE_BINS, labels=TENURE_LABELS, right=False).value_counts()
    return {"labels": TENURE_LABELS, "values": [int(c.get(lbl, 0)) for lbl in TENURE_LABELS]}


def unit_performance(scope: Scope = None) -> pd.DataFrame:
    """One row per unit: rent vs its property's median, tenant tenure, last billed month, idle months."""
    now = today()
    units = _df(_scoped(
        db.select(Unit.id, Unit.property_id, Property.name.label("property"), Unit.unit_no, Unit.status,
                  Unit.monthly_rent.label("rent"))
        .join(Property, Property.id == Unit.property_id).order_by(Property.name, Unit.unit_no),
        scope,
    ))
    if units.empty:
        return pd.DataFrame(columns=["property", "unit_no", "status", "rent", "median", "vs_median_pct",
                                     "tenant", "tenure_months", "last_billed", "idle_months"])
    units["rent"] = _num(units["rent"])
    active = _df(
        db.select(Lease.unit_id, Lease.start_date, User.name.label("tenant"))
        .join(Tenant, Tenant.id == Lease.tenant_id).join(User, User.id == Tenant.user_id)
        .where(Lease.status == "active")
    ).set_index("unit_id")
    last = _df(
        db.select(Lease.unit_id, db.func.max(Charge.period).label("last_billed"))
        .select_from(Charge).join(Lease, Lease.id == Charge.lease_id).group_by(Lease.unit_id)
    ).set_index("unit_id")

    units["median"] = units.groupby("property_id")["rent"].transform("median")
    units["vs_median_pct"] = ((units["rent"] / units["median"] - 1) * 100).round(1)
    units["tenant"] = units["id"].map(active["tenant"])
    start = units["id"].map(active["start_date"])
    units["tenure_months"] = start.map(lambda d: round((now - d).days / DAYS_PER_MONTH, 1) if pd.notna(d) else None)
    units["last_billed"] = units["id"].map(last["last_billed"])
    units["idle_months"] = [
        _months_between(now, lb) if s != "occupied" and pd.notna(lb) else None
        for s, lb in zip(units["status"], units["last_billed"])
    ]
    return units[["property", "unit_no", "status", "rent", "median", "vs_median_pct",
                  "tenant", "tenure_months", "last_billed", "idle_months"]]


def rent_stats(perf: pd.DataFrame | None = None) -> list[dict]:
    """Asking-rent spread per property, and occupied vs vacant averages."""
    perf = unit_performance() if perf is None else perf
    if perf.empty:
        return []
    out = perf.groupby("property")["rent"].agg(units="size", min="min", median="median", mean="mean", max="max")
    out["occupied_avg"] = perf[perf["status"] == "occupied"].groupby("property")["rent"].mean()
    out["vacant_avg"] = perf[perf["status"] == "vacant"].groupby("property")["rent"].mean()
    return records(out.round(2).reset_index())


def demand(scope: Scope = None, days: int = 30, months: int = 12) -> dict:
    """Listing views and inquiries: per vacant unit (last `days`) and per month (last `months`)."""
    since = datetime.combine(today() - relativedelta(days=days), time.min)
    periods = last_n_periods(months)
    first = datetime.combine(periods[0], time.min)

    def per_unit(model, ts):
        return dict(db.session.execute(_scoped(
            db.select(model.unit_id, db.func.count(model.id)).join(Unit, Unit.id == model.unit_id)
            .where(ts >= since).group_by(model.unit_id), scope)).all())

    def per_month(model, ts):
        m = db.func.date_trunc("month", ts)
        rows = db.session.execute(_scoped(
            db.select(m, db.func.count(model.id)).join(Unit, Unit.id == model.unit_id)
            .where(ts >= first).group_by(m), scope)).all()
        got = {r[0].date(): r[1] for r in rows}
        return [int(got.get(p, 0)) for p in periods]

    views, inquiries = per_unit(UnitView, UnitView.viewed_at), per_unit(Inquiry, Inquiry.created_at)
    open_q = _scoped(db.select(db.func.count(Inquiry.id)).join(Unit, Unit.id == Inquiry.unit_id)
                     .where(Inquiry.status == "new"), scope)
    vacant = db.session.execute(_scoped(
        db.select(Unit.id, Property.name, Unit.unit_no, Unit.monthly_rent, db.func.count(UnitPhoto.id))
        .join(Property, Property.id == Unit.property_id).outerjoin(UnitPhoto, UnitPhoto.unit_id == Unit.id)
        .where(Unit.status == "vacant").group_by(Unit.id, Property.name).order_by(Property.name, Unit.unit_no),
        scope)).all()
    rows = []
    for uid, prop, no, rent, photo_count in vacant:
        v, q = views.get(uid, 0), inquiries.get(uid, 0)
        rows.append({"unit_id": uid, "property": prop, "unit_no": no, "rent": float(rent), "photos": int(photo_count),
                     "views": v, "inquiries": q, "rate": round(q / v * 100, 1) if v else None})
    rows.sort(key=lambda r: (-r["inquiries"], -r["views"]))
    total_v, total_q = sum(views.values()), sum(inquiries.values())
    return {
        "days": days,
        "views": total_v,
        "inquiries": total_q,
        "rate": round(total_q / total_v * 100, 1) if total_v else 0.0,
        "open": int(db.session.scalar(open_q)),
        "rows": rows,
        "monthly": {"labels": [p.strftime("%b") for p in periods],
                    "views": per_month(UnitView, UnitView.viewed_at),
                    "inquiries": per_month(Inquiry, Inquiry.created_at)},
    }


def insights(scope: Scope = None) -> list[tuple[str, str]]:
    """Rule-based insights. Honest name: IF-rules over the data, not AI."""
    out: list[tuple[str, str]] = []
    k = kpis(scope)
    cfg = current_app.config
    if k["leases_expired"]:
        out.append(("bad", f"{k['leases_expired']} lease(s) are past their end date but still active. Renew or end them."))
    if k["leases_ending"]:
        out.append(("warn", f"{k['leases_ending']} lease(s) end within {cfg['LEASE_WARN_DAYS']} days. Confirm renewals now to avoid vacancies."))
    if k["at_risk_rent"] and k["rent_roll"]:
        share = round(k["at_risk_rent"] / k["rent_roll"] * 100)
        out.append(("warn", f"{money(k['at_risk_rent'])} per month ({share}% of the rent roll) comes from "
                            f"{k['at_risk_leases']} lease(s) ending within {RISK_DAYS} days."))
    if k["billed_change_pct"] is not None and k["billed_change_pct"] <= -10:
        out.append(("warn", f"Billing fell {abs(k['billed_change_pct'])}% compared with last month."))
    if k["vacant"]:
        out.append(("info", f"{k['vacant']} vacant unit(s) = {money(k['vacancy_loss'])} of rent not earned every month."))
    if k["maintenance"]:
        out.append(("info", f"{k['maintenance']} unit(s) under maintenance are not earning rent."))

    perf = unit_performance(scope)
    idle = perf[perf["idle_months"].fillna(0) >= 3]
    if len(idle):
        out.append(("warn", f"{len(idle)} unit(s) have not been billed for 3+ months: "
                            + ", ".join(f"{p} {u}" for p, u in zip(idle["property"], idle["unit_no"])) + "."))
    pricey = perf[(perf["status"] == "vacant") & (perf["vs_median_pct"] >= 15)]
    for p, u, pct in zip(pricey["property"], pricey["unit_no"], pricey["vs_median_pct"]):
        out.append(("info", f"Vacant {p} {u} asks {pct}% more than its property's median rent. The price may be why it is empty."))
    cheap = perf[(perf["status"] == "occupied") & (perf["vs_median_pct"] <= -15)]
    if len(cheap):
        out.append(("info", f"{len(cheap)} occupied unit(s) rent 15%+ below their property's median. Review at renewal."))

    d = demand(scope)
    if d["open"]:
        out.append(("warn", f"{d['open']} guest inquir{'y is' if d['open'] == 1 else 'ies are'} waiting for a reply."))
    cold = [r for r in d["rows"] if r["views"] >= 10 and not r["inquiries"]]
    for r in cold:
        out.append(("info", f"{r['property']} {r['unit_no']} had {r['views']} listing views but no inquiries in "
                            f"{d['days']} days. Check its price and photos."))
    bare = [f"{r['property']} {r['unit_no']}" for r in d["rows"] if not r["photos"]]
    if bare:
        out.append(("info", f"{len(bare)} vacant unit(s) have no photos on their listing: {', '.join(bare)}."))

    f = forecast(6, scope)
    if f["contracted"][0] and f["contracted"][-1] < f["contracted"][0] * 0.8:
        drop = round((1 - f["contracted"][-1] / f["contracted"][0]) * 100)
        out.append(("warn", f"Contracted rent drops {drop}% by {f['labels'][-1]} if no lease is renewed."))

    if not isinstance(scope, int):
        bp = by_property(scope)
        if bp["labels"] and bp["rent_roll"][0] > 0:
            out.append(("info", f"{bp['labels'][0]} has the largest rent roll ({money(bp['rent_roll'][0])} per month)."))
        for n, o in zip(bp["labels"], bp["occupancy"]):
            if o < 70:
                out.append(("warn", f"{n} is only {o}% occupied."))
    elif k["units_total"] and k["occupancy_rate"] < 70:
        out.append(("warn", f"This property is only {k['occupancy_rate']}% occupied."))
    if not out:
        out.append(("ok", "No issues found."))
    return out
