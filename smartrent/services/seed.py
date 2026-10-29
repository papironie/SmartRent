"""Demo data: 3 properties (with map coordinates), 25 units with photos, 20 tenants, ~12 months of billing,
a superadmin, two landlords (Maria owns Acacia + Narra, Pedro owns Molave), guest accounts,
and a year of listing views and inquiries.

Same seed number = same data every run (repeatable demo). WIPES all data first.
"""
import os
import random
import shutil
from datetime import datetime, time

from dateutil.relativedelta import relativedelta

from ..extensions import db
from ..models import Charge, Inquiry, Lease, Notification, Property, Tenant, Unit, UnitPhoto, UnitView, User
from ..utils import month_start, today
from . import billing, photos

LANDLORDS = [  # name, email (password landlord123)
    ("Maria Santos", "landlord@smartrent.test"),
    ("Pedro Reyes", "landlord2@smartrent.test"),
]
PROPS = [  # name, address, lat, lng, units, rent range, unit prefix, landlord index
    ("Acacia Apartments", "12 Acacia St., Poblacion", 15.9761, 120.5711, 10, (7000, 9000), "A", 0),
    ("Narra Townhomes", "45 Narra Ave., San Isidro", 15.9843, 120.5602, 6, (12000, 15000), "T", 0),
    ("Molave Boarding House", "8 Molave Rd., Riverside", 15.9695, 120.5827, 9, (3500, 4500), "R", 1),
]
NAMES = [
    "Juan Dela Cruz", "Ana Reyes", "Carlo Mendoza", "Bea Villanueva", "Miguel Bautista", "Liza Garcia",
    "Paolo Ramos", "Kristine Aquino", "Rafael Castillo", "Jasmine Flores", "Mark Navarro", "Nicole Torres",
    "Joshua Soriano", "Camille Domingo", "Adrian Lim", "Patricia Cruz", "Renz Salazar", "Erika Manalo",
    "Vince Pascual", "Grace Ocampo",
]


GUESTS = ["Lea Santiago", "Marco Dizon", "Janelle Uy", "Ken Robles", "Sofia Tan", "Diego Cabrera", "Ivy Medina"]
SEED_PHOTOS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seed_photos")
ROOMS = [("living", 6, "Living area"), ("bedroom", 2, "Bedroom"), ("kitchen", 3, "Kitchen"), ("bathroom", 2, "Bathroom")]
MESSAGES = [
    "Hi, is this unit still available? I would like to schedule a viewing.",
    "Good day! Are pets allowed? I have a small dog.",
    "Is water and electricity included in the rent?",
    "Can I move in next month? I work nearby.",
    "Hello, is parking available for a motorcycle?",
]


def wipe() -> None:
    """Empty every table, restart ids at 1 (like MySQL TRUNCATE did) and delete uploaded photos."""
    models = (UnitView, Inquiry, UnitPhoto, Notification, Charge, Lease, Tenant, Unit, Property, User)
    names = ", ".join(m.__tablename__ for m in models)
    db.session.execute(db.text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    db.session.commit()
    shutil.rmtree(photos.folder(), ignore_errors=True)


def _photos(units: list[Unit], rnd: random.Random) -> None:
    """Copy the bundled sample photos once, then give every unit a living area, bedroom, kitchen and bathroom shot."""
    folder = photos.folder()
    for name in os.listdir(SEED_PHOTOS):
        if name.endswith(".jpg"):
            shutil.copyfile(os.path.join(SEED_PHOTOS, name), os.path.join(folder, "seed-" + name))
    for u in units:
        pos = 0
        for room, count, caption in ROOMS:
            db.session.add(UnitPhoto(unit_id=u.id, filename=f"seed-{room}-{rnd.randint(1, count)}.jpg", caption=caption, position=pos))
            pos += 1
        if u.unit_no.startswith("A"):
            db.session.add(UnitPhoto(unit_id=u.id, filename="seed-building-1.jpg", caption="Building", position=pos))


def _demand(units: list[Unit], guests: list[User], now, rnd: random.Random) -> None:
    """Listing views and inquiries: vacant units all year, let units in the 2 months before their lease."""
    views, inquiries = [], []
    cold = next((u for u in units if u.status == "vacant"), None)  # one listing people look at but never ask about
    year_ago = now - relativedelta(months=12)
    for u in units:
        lease = u.leases[0] if u.leases else None
        if u.status == "maintenance":
            continue
        start = year_ago if lease is None else max(year_ago, lease.start_date - relativedelta(months=2))
        end = now if lease is None else lease.start_date
        days = (end - start).days
        if days <= 0:
            continue
        per_week = rnd.uniform(4, 9) if lease is None else rnd.uniform(6, 12)
        for _ in range(int(days / 7 * per_week)):
            at = datetime.combine(start + relativedelta(days=rnd.randrange(days)), time(rnd.randint(7, 22), rnd.randint(0, 59)))
            views.append({"unit_id": u.id, "user_id": None, "viewed_at": at})
            if u is not cold and rnd.random() < 0.06:
                guest = rnd.choice(guests)
                age = (now - at.date()).days
                status = "new" if age <= 10 else ("contacted" if age <= 45 and lease is None else "closed")
                inquiries.append({"unit_id": u.id, "user_id": guest.id, "message": rnd.choice(MESSAGES),
                                  "phone": "09" + str(rnd.randint(100000000, 999999999)), "status": status,
                                  "viewing_date": at.date() + relativedelta(days=rnd.randint(2, 10)), "created_at": at})
    # A guest has at most one open inquiry per unit (the app enforces this too).
    seen = set()
    for q in sorted(inquiries, key=lambda q: q["created_at"], reverse=True):
        key = (q["unit_id"], q["user_id"])
        if q["status"] != "closed" and key in seen:
            q["status"] = "closed"
        seen.add(key)
    if views:
        db.session.execute(db.insert(UnitView), views)
    if inquiries:
        db.session.execute(db.insert(Inquiry), inquiries)


def run() -> dict:
    rnd = random.Random(42)
    wipe()
    now = today()

    sup = User(name="Ramon Villareal", email="superadmin@smartrent.test", role="superadmin")
    sup.set_password("super123")
    landlords = []
    for name, email in LANDLORDS:
        u = User(name=name, email=email, role="landlord")
        u.set_password("landlord123")
        landlords.append(u)
    db.session.add_all([sup, *landlords])
    db.session.flush()
    guests = []
    for i, name in enumerate(["Gina Lopez", *GUESTS]):
        email = "guest@smartrent.test" if i == 0 else name.lower().replace(" ", ".") + "@guest.test"
        g = User(name=name, email=email, role="guest")
        g.set_password("guest123")
        guests.append(g)
    db.session.add_all(guests)

    unit_rows: list[Unit] = []
    owner_of: dict[int, int] = {}  # property id -> landlord user id
    for name, addr, lat, lng, count, (lo, hi), prefix, who in PROPS:
        prop = Property(owner_id=landlords[who].id, name=name, address=addr)
        prop.set_location(lat, lng)
        db.session.add(prop)
        db.session.flush()
        owner_of[prop.id] = prop.owner_id
        for i in range(1, count + 1):
            rent = round(rnd.randint(lo, hi) / 500) * 500
            u = Unit(property_id=prop.id, unit_no=f"{prefix}-{i:02d}", monthly_rent=rent)
            db.session.add(u)
            unit_rows.append(u)
    db.session.flush()

    rnd.shuffle(unit_rows)
    unit_rows.pop().status = "maintenance"

    for i, name in enumerate(NAMES):
        email = name.lower().replace(" ", ".") + "@mail.test"
        user = User(name=name, email=email, role="tenant")
        user.set_password("tenant123")
        db.session.add(user)
        db.session.flush()
        unit = unit_rows[i]
        tenant = Tenant(
            user_id=user.id,
            landlord_id=owner_of[unit.property_id],  # the tenant belongs to the landlord of their unit
            phone="09" + str(rnd.randint(100000000, 999999999)),
            emergency_name="Family contact",
            emergency_phone="09" + str(rnd.randint(100000000, 999999999)),
        )
        db.session.add(tenant)
        db.session.flush()

        start = month_start(now) - relativedelta(months=rnd.randint(3, 13))
        end = start + relativedelta(years=1, days=-1)
        if end < now:
            end += relativedelta(years=1)  # renewed
        if i < 2:
            end = now + relativedelta(days=10 + i * 8)  # 2 leases ending soon
        rent = float(unit.monthly_rent)
        db.session.add(Lease(
            tenant_id=tenant.id, unit_id=unit.id, start_date=start, end_date=end,
            monthly_rent=rent, deposit=rent * 2, due_day=rnd.choice([1, 5, 5, 10, 15, 30]),
        ))
        unit.status = "occupied"
    db.session.commit()

    extra = random.Random(7)  # separate generator: the lease/billing data stays identical to before
    all_units = db.session.scalars(db.select(Unit).order_by(Unit.id)).all()
    _photos(all_units, extra)
    _demand(all_units, guests, now, extra)
    db.session.commit()

    # Bill every month from the earliest lease start up to this month.
    first = db.session.scalar(db.select(db.func.min(Lease.start_date)))
    p = month_start(first)
    while p <= month_start(now):
        billing.generate(p, send_notifications=False)
        p += relativedelta(months=1)

    return {
        "properties": db.session.scalar(db.select(db.func.count(Property.id))),
        "units": db.session.scalar(db.select(db.func.count(Unit.id))),
        "tenants": db.session.scalar(db.select(db.func.count(Tenant.id))),
        "charges": db.session.scalar(db.select(db.func.count(Charge.id))),
        "photos": db.session.scalar(db.select(db.func.count(UnitPhoto.id))),
        "guests": len(guests),
        "listing_views": db.session.scalar(db.select(db.func.count(UnitView.id))),
        "inquiries": db.session.scalar(db.select(db.func.count(Inquiry.id))),
    }
