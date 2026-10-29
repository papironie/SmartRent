from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from smartrent.extensions import db
from smartrent.models import Charge, Lease, Unit
from conftest import login


def admin_client(client, make):
    make.admin()
    assert login(client, "admin@t.test", "admin123").status_code == 302
    return client


# ---------- access control ----------
def test_anonymous_is_redirected(client):
    for path in ("/admin/", "/admin/tenants", "/admin/properties/", "/api/analytics", "/tenant/", "/admin/reports/tenants.csv"):
        r = client.get(path)
        assert r.status_code == 302 and "/login" in r.headers["Location"], path


def test_tenant_cannot_reach_admin_or_api(client, make):
    make.tenant("Jo")
    from smartrent.models import User
    email = db.session.scalar(db.select(User.email).where(User.role == "tenant"))
    login(client, email, "tenant123")
    for path in ("/admin/", "/admin/leases", "/admin/properties/", "/api/analytics", "/api/properties.geojson",
                 "/admin/reports/tenants.xlsx", "/admin/properties/import"):
        assert client.get(path).status_code == 403, path
    assert client.get("/tenant/").status_code == 200


def test_admin_cannot_use_tenant_portal(client, make):
    admin_client(client, make)
    assert client.get("/tenant/").status_code == 403


def test_tenant_sees_only_own_data(client, make):
    p = make.prop()
    mine = make.tenant("Mine"); other = make.tenant("Other")
    make.lease(mine, make.unit(p, "1", 4111), due_day=5)
    make.lease(other, make.unit(p, "2", 9777), due_day=5)
    from smartrent.services import billing
    billing.generate(date(2026, 9, 11))
    login(client, mine.user.email, "tenant123")
    html = client.get("/tenant/").data.decode()
    assert "4,111.00" in html and "9,777.00" not in html and "Other" not in html


def test_csrf_enforced_on_post(app, client, make):
    admin_client(client, make)
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/admin/", data={}).status_code == 400
    assert client.post("/logout", data={}).status_code == 400


def test_login_wrong_password_and_logout(client, make):
    make.admin()
    r = login(client, "admin@t.test", "nope")
    assert r.status_code == 200 and b"wrong" in r.data
    login(client, "admin@t.test", "admin123")
    assert client.get("/admin/").status_code == 200
    client.post("/logout")
    assert client.get("/admin/").status_code == 302


def test_every_admin_page_renders_with_data(client, make):
    p = make.prop("P", 15.97, 120.57); t = make.tenant(); make.lease(t, make.unit(p))
    admin_client(client, make)
    client.post("/admin/charges", data={"month": "2026-09"})
    for path in ("/admin/", "/admin/properties/", f"/admin/properties/{p.id}", "/admin/properties/import",
                 "/admin/tenants", f"/admin/tenants/{t.id}", "/admin/leases", "/admin/leases?show=all",
                 "/admin/charges?month=2026-09", "/admin/analytics", "/admin/reports/",
                 "/api/analytics", "/api/properties.geojson"):
        assert client.get(path).status_code == 200, path
    assert client.get("/admin/tenants/9999").status_code == 404
    assert client.get("/admin/properties/9999").status_code == 404


# ---------- leases ----------
def _lease_form(tenant, unit, **over):
    d = {"action": "create", "tenant_id": tenant.id, "unit_id": unit.id, "start_date": "2026-09-01",
         "end_date": "2027-08-31", "deposit": "10000", "due_day": "5", "monthly_rent": ""}
    d.update(over)
    return d


def test_create_lease_marks_unit_occupied_and_bills(client, make):
    p = make.prop(); u = make.unit(p, rent=6500); t = make.tenant()
    admin_client(client, make)
    client.post("/admin/leases", data=_lease_form(t, u))
    assert db.session.get(Unit, u.id).status == "occupied"
    lease = db.session.scalar(db.select(Lease))
    assert float(lease.monthly_rent) == 6500.0                  # unit rate used when rent field is blank
    assert db.session.scalar(db.select(db.func.count(Charge.id))) == 1


def test_cannot_double_lease_unit_or_tenant(client, make):
    p = make.prop(); u1, u2 = make.unit(p, "1"), make.unit(p, "2"); t1, t2 = make.tenant(), make.tenant()
    admin_client(client, make)
    client.post("/admin/leases", data=_lease_form(t1, u1))
    client.post("/admin/leases", data=_lease_form(t2, u1))      # unit taken
    client.post("/admin/leases", data=_lease_form(t1, u2))      # tenant already has one
    assert db.session.scalar(db.select(db.func.count(Lease.id))) == 1


@pytest.mark.parametrize("over", [{"end_date": "2026-08-01"}, {"due_day": "32"}, {"due_day": "0"}, {"start_date": "garbage"},
                                  {"deposit": "-5"}, {"monthly_rent": "-1"}])
def test_invalid_lease_rejected(client, make, over):
    p = make.prop(); u = make.unit(p); t = make.tenant()
    admin_client(client, make)
    client.post("/admin/leases", data=_lease_form(t, u, **over))
    assert db.session.scalar(db.select(db.func.count(Lease.id))) == 0
    assert db.session.get(Unit, u.id).status == "vacant"


def test_database_blocks_second_active_lease(make):
    p = make.prop(); u = make.unit(p); make.lease(make.tenant(), u)
    with pytest.raises(IntegrityError):
        make.lease(make.tenant(), u)          # partial unique index, not app code
    db.session.rollback()


def test_renew_and_end_lease(client, make):
    p = make.prop(); u = make.unit(p); l = make.lease(make.tenant(), u, end=date(2026, 12, 31))
    admin_client(client, make)
    client.post("/admin/leases", data={"action": "renew", "lease_id": l.id, "months": "6"})
    assert db.session.get(Lease, l.id).end_date == date(2027, 6, 30)
    client.post("/admin/leases", data={"action": "end", "lease_id": l.id})
    assert db.session.get(Lease, l.id).status == "ended" and db.session.get(Unit, u.id).status == "vacant"


def test_billing_page_refuses_future_month(client, make):
    p = make.prop(); make.lease(make.tenant(), make.unit(p))
    admin_client(client, make)
    client.post("/admin/charges", data={"month": "2027-01"})
    assert db.session.scalar(db.select(db.func.count(Charge.id))) == 0


# ---------- tenants ----------
def test_add_tenant_and_duplicate_email(client, make):
    admin_client(client, make)
    form = {"name": "New Person", "email": "new@x.test", "phone": "0917"}
    r = client.post("/admin/tenants", data=form, follow_redirects=True)
    assert b"Temporary password" in r.data
    r = client.post("/admin/tenants", data=form, follow_redirects=True)
    assert b"already has an account" in r.data
    assert b"valid email" in client.post("/admin/tenants", data={**form, "email": "bad"}, follow_redirects=True).data


# ---------- properties & units ----------
def test_unit_status_rules(client, make):
    p = make.prop(); free = make.unit(p, "1"); taken = make.unit(p, "2"); make.lease(make.tenant(), taken)
    admin_client(client, make)
    url = f"/admin/properties/{p.id}"
    client.post(url, data={"action": "update_unit", "unit_id": free.id, "status": "occupied", "monthly_rent": "5000"})
    assert db.session.get(Unit, free.id).status == "vacant"            # cannot hand-set occupied
    client.post(url, data={"action": "update_unit", "unit_id": taken.id, "status": "vacant", "monthly_rent": "5000"})
    assert db.session.get(Unit, taken.id).status == "occupied"         # cannot vacate leased unit
    client.post(url, data={"action": "update_unit", "unit_id": free.id, "status": "maintenance", "monthly_rent": "5500"})
    u = db.session.get(Unit, free.id)
    assert u.status == "maintenance" and float(u.monthly_rent) == 5500.0


def test_duplicate_unit_and_delete_leased_unit_are_refused(client, make):
    p = make.prop(); u = make.unit(p, "1"); make.lease(make.tenant(), u)
    admin_client(client, make)
    url = f"/admin/properties/{p.id}"
    r = client.post(url, data={"action": "add_unit", "unit_no": "1", "monthly_rent": "100"}, follow_redirects=True)
    assert b"Not allowed" in r.data
    r = client.post(url, data={"action": "delete_unit", "unit_id": u.id}, follow_redirects=True)
    assert b"Not allowed" in r.data and db.session.get(Unit, u.id) is not None


def test_cannot_edit_unit_through_another_property(client, make):
    a, b = make.prop("A"), make.prop("B"); u = make.unit(a, "1")
    admin_client(client, make)
    r = client.post(f"/admin/properties/{b.id}", data={"action": "delete_unit", "unit_id": u.id})
    assert r.status_code == 404 and db.session.get(Unit, u.id) is not None


# ---------- PostGIS ----------
def test_location_roundtrip_geojson_and_nearby(client, make):
    a = make.prop("A", 15.9761, 120.5711); b = make.prop("B", 15.9843, 120.5602); far = make.prop("Far", 14.5995, 120.9842)
    nopin = make.prop("NoPin")
    admin_client(client, make)
    gj = client.get("/api/properties.geojson").json
    assert {f["properties"]["name"] for f in gj["features"]} == {"A", "B", "Far"}
    f = next(f for f in gj["features"] if f["properties"]["name"] == "A")
    assert f["geometry"]["coordinates"] == [120.5711, 15.9761]           # GeoJSON is [lng, lat]
    html = client.get(f"/admin/properties/{a.id}").data.decode()
    assert ">B</a>" in html and ">Far</a>" not in html and ">NoPin</a>" not in html
    assert "no map pin" in client.get("/admin/properties/").data.decode()


def test_edit_location_and_reject_bad_coordinates(client, make):
    p = make.prop("A", 15.0, 120.0)
    admin_client(client, make)
    url = f"/admin/properties/{p.id}"
    base = {"action": "save_property", "name": "A", "address": "x"}
    client.post(url, data={**base, "latitude": "16.5", "longitude": "121.25"})
    db.session.expire_all()
    got = db.session.get(Property_, p.id)
    assert (round(got.lat, 4), round(got.lng, 4)) == (16.5, 121.25)
    client.post(url, data={**base, "latitude": "95", "longitude": "121"})          # latitude out of range
    client.post(url, data={**base, "latitude": "abc", "longitude": "121"})
    db.session.expire_all()
    assert round(db.session.get(Property_, p.id).lat, 4) == 16.5
    client.post(url, data={**base, "latitude": "", "longitude": ""})               # clearing the pin
    db.session.expire_all()
    assert db.session.get(Property_, p.id).lat is None


from smartrent.models import Property as Property_  # noqa: E402
