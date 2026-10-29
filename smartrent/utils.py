"""Small helpers shared by routes, services and templates."""
from datetime import date, datetime

from dateutil import parser as dateparser
from dateutil.relativedelta import relativedelta
from flask import current_app


def today() -> date:
    """Real date, or the frozen demo date from config (TODAY)."""
    frozen = current_app.config.get("TODAY")
    return frozen if frozen else date.today()


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return d + relativedelta(day=31)  # relativedelta clamps to the real last day


def add_months(d: date, n: int) -> date:
    return d + relativedelta(months=n)


def last_n_periods(n: int = 12) -> list[date]:
    """First day of each of the last n months, oldest first, ending this month."""
    start = month_start(today())
    return [start - relativedelta(months=i) for i in range(n - 1, -1, -1)]


def parse_date(raw: str | None) -> date | None:
    """Strict-ish date parse for form input. Returns None when invalid."""
    if not raw or not raw.strip():
        return None
    try:
        return dateparser.parse(raw.strip(), dayfirst=False).date()
    except (ValueError, OverflowError):
        return None


def parse_month(raw: str | None) -> date | None:
    """'2026-09' -> date(2026, 9, 1)."""
    d = parse_date((raw or "") + "-01") if raw and len(raw) == 7 else None
    return d


def to_float(raw, default: float = 0.0) -> float:
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


# ---------- Jinja filters ----------
def money(n) -> str:
    return "₱{:,.2f}".format(float(n or 0))


def fmt_date(d) -> str:
    if not d:
        return "—"
    if isinstance(d, datetime):
        d = d.date()
    return f"{d:%b} {d.day}, {d.year}"


def month_label(d) -> str:
    return f"{d:%b %Y}" if d else "—"


def ordinal(n: int) -> str:
    n = int(n)
    if n % 100 in (11, 12, 13):
        return f"{n}th"
    return f"{n}{({1: 'st', 2: 'nd', 3: 'rd'}).get(n % 10, 'th')}"


def register_filters(app):
    app.add_template_filter(money, "money")
    app.add_template_filter(fmt_date, "fmt_date")
    app.add_template_filter(month_label, "month_label")
    app.add_template_filter(ordinal, "ordinal")
