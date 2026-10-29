import os
from datetime import date
from io import BytesIO

import pytest

from smartrent.cli import init_database
from smartrent.extensions import db
from smartrent.models import Inquiry, Notification, UnitPhoto, UnitView, User
from smartrent.services import analytics
from conftest import login

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def user(role, email, pw="secret123", name=None):
    u = User(name=name or role.title(), email=email, role=role)
    u.set_password(pw)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def uploads(app, tmp_path):
    app.config["UPLOAD_DIR"] = str(tmp_path)
    return tmp_path


# ---------- public listings ----------
def test_anyone_can_browse_vacant_units_only(client, make):
    p = make.prop("Acacia")
    free = make.unit(p, "A-1", 7000)
    taken = make.unit(p, "A-2", 9000)
    make.lease(make.tenant(), taken)
    assert client.get("/").headers["Location"].endswith("/units")
    html = client.get("/units").data.decode()
    assert "A-1" in html and "A-2" not in html
    assert client.get(f"/units/{free.id}").status_code == 200
    assert client.get(f"/units/{taken.id}").status_code == 404        # occupied units stay private
    assert "A-1" not in client.get("/units?max_rent=6000").data.decode()
    assert db.session.scalar(db.select(db.func.count(UnitView.id))) == 1


def test_staff_views_are_not_counted_and_staff_can_preview(client, make):
    p = make.prop(); taken = make.unit(p)
    make.lease(make.tenant(), taken)
    make.admin(); login(client, "admin@t.test", "admin123")
    assert client.get(f"/units/{taken.id}").status_code == 200
    assert db.session.scalar(db.select(db.func.count(UnitView.id))) == 0


# ---------- guest accounts ----------
def test_register_guest_and_validation(client, make):
    r = client.post("/register", data={"name": "Gia", "email": "gia@x.test", "password": "longenough", "confirm": "longenough"})
    assert r.status_code == 302
    assert db.session.scalar(db.select(User.role).where(User.email == "gia@x.test")) == "guest"
    assert client.get("/my-inquiries").status_code == 200
    client.post("/logout")
    r = client.post("/register", data={"name": "X", "email": "gia@x.test", "password": "short", "confirm": "short"})
    assert b"already has an account" in r.data and b"at least 8" in r.data


def test_login_next_only_follows_local_paths(client, make):
    user("guest", "g@x.test")
    assert login(client, "g@x.test", "secret123").headers["Location"] == "/"
    client.post("/logout")
    r = client.post("/login", data={"email": "g@x.test", "password": "secret123", "next": "//evil.test/x"})
    assert "evil" not in r.headers["Location"]
    client.post("/logout")
    r = client.post("/login", data={"email": "g@x.test", "password": "secret123", "next": "/units/5"})
    assert r.headers["Location"].endswith("/units/5")


def test_guest_inquiry_flow(client, make):
    p = make.prop("Acacia"); u = make.unit(p, "A-1")
    make.admin()
    user("guest", "g@x.test")
    assert client.post(f"/units/{u.id}/inquire", data={"message": "Hi"}).status_code == 302   # signed out -> login
    login(client, "g@x.test", "secret123")
    assert client.get("/admin/").status_code == 403 and client.get("/tenant/").status_code == 403
    client.post(f"/units/{u.id}/inquire", data={"message": "Hi", "viewing_date": "2026-01-01"})   # past date
    assert db.session.scalar(db.select(db.func.count(Inquiry.id))) == 0
    client.post(f"/units/{u.id}/inquire", data={"message": "Is it free?", "phone": "0917", "viewing_date": "2026-09-20"})
    client.post(f"/units/{u.id}/inquire", data={"message": "Again"})                                # open one exists
    rows = db.session.scalars(db.select(Inquiry)).all()
    assert len(rows) == 1 and rows[0].viewing_date == date(2026, 9, 20)
    assert db.session.scalar(db.select(Notification.message).where(Notification.type == "inquiry")).startswith("Guest asked")
    assert b"Is it free?" in client.get("/my-inquiries").data


def test_tenant_cannot_send_inquiry(client, make):
    p = make.prop(); u = make.unit(p)
    t = make.tenant()
    login(client, t.user.email, "tenant123")
    assert client.post(f"/units/{u.id}/inquire", data={"message": "x"}).status_code == 403


def test_admin_updates_inquiry_status(client, make):
    p = make.prop(); u = make.unit(p)
    g = user("guest", "g@x.test")
    db.session.add(Inquiry(unit_id=u.id, user_id=g.id, message="hi")); db.session.commit()
    make.admin(); login(client, "admin@t.test", "admin123")
    assert b"hi" in client.get("/admin/inquiries").data
    iid = db.session.scalar(db.select(Inquiry.id))
    client.post("/admin/inquiries", data={"inquiry_id": iid, "status": "contacted"})
    db.session.expire_all()
    assert db.session.get(Inquiry, iid).status == "contacted"
    assert client.post("/admin/inquiries", data={"inquiry_id": iid, "status": "bogus"}).status_code == 400


# ---------- photos ----------
def test_upload_cover_delete_photos(client, make, uploads):
    p = make.prop(); u = make.unit(p, "A-1")
    make.admin(); login(client, "admin@t.test", "admin123")
    url = f"/admin/properties/{p.id}/units/{u.id}/photos"
    client.post(url, data={"action": "upload", "caption": "Living room",
                           "photos": [(BytesIO(PNG), "a.png"), (BytesIO(JPG), "b.jpg"), (BytesIO(b"<?php"), "evil.jpg")]},
                content_type="multipart/form-data")
    rows = db.session.scalars(db.select(UnitPhoto).order_by(UnitPhoto.position)).all()
    assert [r.filename.rsplit(".", 1)[1] for r in rows] == ["png", "jpg"]          # the fake .jpg was refused
    assert all(os.path.exists(uploads / "units" / r.filename) for r in rows)
    assert client.get(f"/media/units/{rows[0].filename}").data == PNG
    assert client.get("/media/units/../../config.py").status_code == 404
    client.post(url, data={"action": "cover", "photo_id": rows[1].id})
    db.session.expire_all()
    assert db.session.get(UnitPhoto, rows[1].id).position == 0
    name = rows[0].filename
    client.post(url, data={"action": "delete", "photo_id": rows[0].id})
    assert not os.path.exists(uploads / "units" / name)
    assert b"Living room" in client.get(f"/units/{u.id}").data


def test_shared_seed_file_survives_until_last_use(make, uploads):
    from smartrent.services import photos
    p = make.prop(); a = make.unit(p, "1"); b = make.unit(p, "2")
    (uploads / "units").mkdir(exist_ok=True)
    (uploads / "units" / "seed-x.jpg").write_bytes(JPG)
    for unit in (a, b):
        db.session.add(UnitPhoto(unit_id=unit.id, filename="seed-x.jpg"))
    db.session.commit()
    photos.delete(a.photos[0]); db.session.commit()
    assert (uploads / "units" / "seed-x.jpg").exists()
    db.session.refresh(b)
    photos.delete(b.photos[0]); db.session.commit()
    assert not (uploads / "units" / "seed-x.jpg").exists()


# ---------- superadmin (platform only) ----------
def test_superadmin_runs_platform_but_not_landlord_pages(client, make):
    user("superadmin", "s@x.test")
    make.prop("Acacia")
    login(client, "s@x.test", "secret123")
    assert client.get("/").headers["Location"].endswith("/superadmin/")
    assert b"Landlord" in client.get("/superadmin/").data
    assert client.get("/superadmin/users").status_code == 200
    for path in ("/admin/", "/admin/analytics", "/admin/properties/", "/api/analytics", "/admin/reports/"):
        assert client.get(path).status_code == 403, path
    client.post("/logout")
    make.admin(); login(client, "admin@t.test", "admin123")
    assert client.get("/superadmin/").status_code == 403 and client.get("/superadmin/users").status_code == 403


def test_superadmin_manages_accounts_safely(client, make):
    me = user("superadmin", "s@x.test")
    lord = make.landlord()
    g = user("guest", "g@x.test")
    login(client, "s@x.test", "secret123")
    r = client.post("/superadmin/users", data={"action": "create", "name": "New", "email": "new@x.test", "role": "landlord"},
                    follow_redirects=True)
    assert b"Temporary password" in r.data
    assert db.session.scalar(db.select(User.role).where(User.email == "new@x.test")) == "landlord"
    client.post("/superadmin/users", data={"action": "create", "name": "T", "email": "t@x.test", "role": "tenant"})
    assert db.session.scalar(db.select(User.id).where(User.email == "t@x.test")) is None    # tenants come from landlords
    client.post("/superadmin/users", data={"action": "toggle_active", "user_id": me.id})       # not yourself
    db.session.expire_all(); assert me.is_active
    client.post("/superadmin/users", data={"action": "delete_guest", "user_id": lord.id})      # only guests
    client.post("/superadmin/users", data={"action": "delete_guest", "user_id": g.id})
    db.session.expire_all()
    assert db.session.get(User, lord.id) is not None and db.session.get(User, g.id) is None


def test_deactivated_landlord_is_signed_out_and_unlisted(app, make):
    user("superadmin", "s@x.test")
    lord = make.landlord()
    u = make.unit(make.prop("Acacia"), "A-1")
    boss, staff, visitor = app.test_client(), app.test_client(), app.test_client()
    login(boss, "s@x.test", "secret123"); login(staff, "admin@t.test", "admin123")
    assert staff.get("/admin/").status_code == 200 and b"A-1" in visitor.get("/units").data
    boss.post("/superadmin/users", data={"action": "toggle_active", "user_id": lord.id})
    assert staff.get("/admin/").status_code == 302                           # signed out on next request
    r = login(staff, "admin@t.test", "admin123")
    assert r.status_code == 200 and b"deactivated" in r.data
    assert b"A-1" not in visitor.get("/units").data and visitor.get(f"/units/{u.id}").status_code == 404
    assert boss.get(f"/units/{u.id}").status_code == 200                      # superadmin can still look


# ---------- landlord isolation ----------
def test_landlords_only_see_their_own_data(client, make):
    maria = make.landlord()
    pedro = make.landlord("pedro@x.test", "pedro123", name="Pedro")
    mp = make.prop("Maria House", 15.97, 120.57); pp = make.prop("Pedro Place", 15.98, 120.58, owner=pedro)
    mu = make.unit(mp, "M-1", 5000); pu = make.unit(pp, "P-1", 7777)
    mt = make.tenant("Maria Tenant"); pt = make.tenant("Pedro Tenant", landlord=pedro)
    make.lease(mt, mu); pl = make.lease(pt, pu)
    g = user("guest", "g@x.test")
    db.session.add(Inquiry(unit_id=make.unit(pp, "P-2", 6100).id, user_id=g.id, message="pedro only")); db.session.commit()
    from smartrent.services import billing
    billing.generate(date(2026, 9, 1))
    login(client, "pedro@x.test", "pedro123")
    pages = {
        "/admin/": [], "/admin/properties/": ["Pedro Place"], "/admin/tenants": ["Pedro Tenant"],
        "/admin/leases": ["Pedro Tenant"], "/admin/charges?month=2026-09": ["7,777"], "/admin/inquiries": ["pedro only"],
        "/admin/analytics": ["Pedro Place"], "/admin/reports/tenants.csv": ["Pedro Tenant"],
        "/admin/reports/rent_roll.csv": ["P-1"], "/api/properties.geojson": ["Pedro Place"],
    }
    for path, want in pages.items():
        body = client.get(path).data.decode()
        assert all(w in body for w in want), path
        leaks = [ln.strip() for ln in body.splitlines() if "Maria" in ln or "M-1" in ln or "5,000" in ln]
        assert not leaks, (path, leaks)
    for path in (f"/admin/properties/{mp.id}", f"/admin/tenants/{mt.id}", f"/api/properties/{mp.id}/site",
                 f"/admin/properties/{mp.id}/units/{mu.id}/photos"):
        assert client.get(path).status_code == 404, path                     # foreign records look missing
    k = client.get(f"/api/analytics?property={mp.id}").get_json()             # foreign filter is ignored
    assert k["property_id"] is None and k["kpis"]["rent_roll"] == 7777
    # cannot touch Maria's records through forms either
    assert client.post("/admin/leases", data={"action": "end", "lease_id": mt.leases[0].id}).status_code == 404
    client.post("/admin/leases", data={"action": "create", "tenant_id": mt.id, "unit_id": make.unit(pp, "P-3", 6200).id,
                                       "start_date": "2026-09-01", "end_date": "2027-08-31", "deposit": "0", "due_day": "5"})
    assert db.session.scalar(db.select(db.func.count()).select_from(Inquiry)) == 1
    from smartrent.models import Lease
    assert db.session.scalar(db.select(db.func.count(Lease.id))) == 2
    assert pl.status == "active"


def test_inquiry_notifies_only_the_units_landlord(client, make):
    maria = make.landlord()
    pedro = make.landlord("pedro@x.test", "pedro123", name="Pedro")
    u = make.unit(make.prop("Pedro Place", owner=pedro), "P-1")
    user("guest", "g@x.test")
    login(client, "g@x.test", "secret123")
    client.post(f"/units/{u.id}/inquire", data={"message": "hello"})
    owners = db.session.scalars(db.select(Notification.user_id).where(Notification.type == "inquiry")).all()
    assert owners == [pedro.id]


def test_upgrade_is_safe_to_run_twice(app):
    with app.app_context():
        init_database(); init_database()


# ---------- demand analytics ----------
def test_demand_counts_views_and_inquiries(make):
    p = make.prop("P"); hot = make.unit(p, "1"); cold = make.unit(p, "2")
    g = user("guest", "g@x.test")
    db.session.add_all([UnitView(unit_id=hot.id) for _ in range(4)] + [UnitView(unit_id=cold.id) for _ in range(10)])
    db.session.add(Inquiry(unit_id=hot.id, user_id=g.id, message="hi"))
    db.session.add(UnitPhoto(unit_id=hot.id, filename="x.jpg"))
    db.session.commit()
    d = analytics.demand()
    assert (d["views"], d["inquiries"], d["open"]) == (14, 1, 1)
    rows = {r["unit_no"]: r for r in d["rows"]}
    assert rows["1"]["rate"] == 25.0 and rows["2"]["rate"] == 0.0 and rows["2"]["photos"] == 0
    text = " ".join(m for _, m in analytics.insights())
    assert "10 listing views but no inquiries" in text and "no photos" in text and "waiting for a reply" in text
