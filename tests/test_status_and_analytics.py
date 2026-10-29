from datetime import date

from smartrent.models import Lease
from smartrent.services import analytics, tenant_status, billing


def test_status_rules(make):
    p = make.prop()
    mk = lambda i, **kw: make.lease(make.tenant(f"T{i}"), make.unit(p, f"U{i}"), **kw)
    assert tenant_status.classify(None) == "no_lease"
    assert tenant_status.classify(mk(1, start=date(2026, 1, 1), end=date(2026, 12, 31))) == "active"
    assert tenant_status.classify(mk(2, start=date(2026, 1, 1), end=date(2026, 10, 1))) == "ending"      # 20 days left
    assert tenant_status.classify(mk(3, start=date(2026, 1, 1), end=date(2026, 9, 1))) == "expired"
    assert tenant_status.classify(mk(4, start=date(2026, 9, 1), end=date(2027, 8, 31))) == "new"
    ended = mk(5, status="ended")
    assert tenant_status.classify(ended) == "no_lease"


def test_expired_beats_new_and_ending_boundary(make):
    p = make.prop()
    boundary = make.lease(make.tenant(), make.unit(p), start=date(2026, 1, 1), end=date(2026, 10, 11))  # exactly 30 days
    assert tenant_status.classify(boundary) == "ending"


def test_kpis_and_properties(make):
    a = make.prop("A"); b = make.prop("B")
    make.lease(make.tenant(), make.unit(a, "1", 8000))
    make.unit(a, "2", 7000)                         # vacant
    make.unit(b, "1", 3000, status="maintenance")
    k = analytics.kpis()
    assert (k["units_total"], k["occupied"], k["vacant"], k["maintenance"]) == (3, 1, 1, 1)
    assert k["occupancy_rate"] == 33.3 and k["rent_roll"] == 8000 and k["vacancy_loss"] == 7000
    bp = analytics.by_property()
    assert bp["labels"][0] == "A" and bp["rent_roll"][0] == 8000 and bp["occupancy"][0] == 50.0


def test_monthly_fills_gaps_with_zero(make):
    p = make.prop(); make.lease(make.tenant(), make.unit(p, rent=1000), start=date(2026, 8, 1))
    billing.generate(date(2026, 8, 1)); billing.generate(date(2026, 9, 1))
    m = analytics.monthly(12)
    assert len(m["labels"]) == 12 and m["billed"][-2:] == [1000.0, 1000.0] and sum(m["billed"][:-2]) == 0


def test_lease_expiry_buckets_and_empty_db(make):
    assert analytics.lease_expiry()["values"] == [0] * 6          # empty database must not crash
    assert analytics.kpis()["occupancy_rate"] == 0.0
    assert analytics.by_property()["labels"] == []
    p = make.prop()
    make.lease(make.tenant(), make.unit(p, "1"), end=date(2026, 9, 1))     # expired -> counted this month
    make.lease(make.tenant(), make.unit(p, "2"), end=date(2026, 11, 20))
    v = analytics.lease_expiry()
    assert v["labels"][0] == "Sep 2026" and v["values"][:3] == [1, 0, 1]


def test_insights_mention_vacancy_and_expired(make):
    p = make.prop(); make.unit(p, "1", 5000)
    make.lease(make.tenant(), make.unit(p, "2"), end=date(2026, 9, 1))
    text = " ".join(m for _, m in analytics.insights())
    assert "vacant" in text and "past their end date" in text
