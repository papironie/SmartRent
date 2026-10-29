# SmartRent (Python edition)

Rental management system: properties and units on a map, tenants, leases, automatic monthly billing, analytics, Excel import/export.
Rewritten from PHP/MySQL to **Python / Flask / PostgreSQL + PostGIS**. **The payment function was removed on purpose** (see section 5).

## 1. What each library does here

| Tool | Where it is used |
|---|---|
| **Flask** | App factory, blueprints (`smartrent/routes/*`), sessions, CLI commands (`flask init-db`, `seed`, `daily`) |
| **SQLAlchemy** (+ Flask-SQLAlchemy) | All tables in `smartrent/models.py`, all queries. No raw string SQL except `TRUNCATE` and `CREATE EXTENSION` |
| **GeoAlchemy2 + PostGIS** | `properties.location` is a real `POINT` column. `ST_DWithin` finds nearby properties, `ST_AsGeoJSON` feeds the map |
| **Jinja2** | Every page in `smartrent/templates/`. Auto-escaping is on (stops XSS) |
| **pandas** | `services/analytics.py` (KPIs, monthly billing, per-property numbers, lease expiry) and CSV reports |
| **openpyxl** | `services/excel.py`: styled `.xlsx` exports and the **Excel import** of properties/units (all-or-nothing validation) |
| **python-dateutil** | `relativedelta` for month math (due-day capping, renewals, "30 days ahead"), `parser` for form dates and the frozen demo date |
| **Leaflet.js** | Map of all properties, click-to-place pin on a property page |
| **Chart.js** | Dashboard and analytics charts, fed by `/api/analytics` |
| **Bootstrap 5** | Layout and components |
| **Vanilla JS** | `static/js/app.js` (charts, maps, site-conditions panel, confirm dialogs) |
| **OpenTopoData API** | Elevation of a property. Fetched once, then cached in PostgreSQL |
| **Open-Meteo API** | Current weather and rain (past 7 days, next 3) for a property |
| Extra (needed) | `psycopg2-binary` (Postgres driver), `Flask-WTF` (CSRF), `requests` (the two API calls), `python-dotenv` (.env), `gunicorn` (production), `pytest` |

Bootstrap, Leaflet and Chart.js are **bundled** in `static/vendor/`, so the site works without a CDN. Map *tiles* come from OpenStreetMap and need internet.

## 2. Run it

Needs Python 3.10+ and PostgreSQL 14+ **with PostGIS**.

**Install PostGIS**
- Windows: install PostgreSQL from postgresql.org, then run **Stack Builder** (Spatial Extensions → PostGIS).
- Ubuntu: `sudo apt install postgresql postgis`
- macOS: `brew install postgresql postgis`

**Create the database**
```
psql -U postgres -c "CREATE ROLE smartrent LOGIN PASSWORD 'smartrent' SUPERUSER"
psql -U postgres -c "CREATE DATABASE smartrent OWNER smartrent"
```
(`SUPERUSER` is only so the app can run `CREATE EXTENSION postgis` itself. If you prefer, run that once as a superuser and make the role a normal one.)

**Run the app**
```
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # Linux/macOS: cp .env.example .env   (then edit SECRET_KEY / DATABASE_URL)
python run.py setup               # enables PostGIS, creates tables, loads demo data (wipes data)
python run.py                     # starts the site
```
Open http://127.0.0.1:5000

`run.py` commands: `python run.py` (start), `setup`, `init` (tables only, keeps data), `seed`, `daily`, `test`.
The `flask init-db / seed / daily` commands still work too (`set FLASK_APP=wsgi.py` first).

| Role | Email | Password |
|---|---|---|
| Superadmin | superadmin@smartrent.test | super123 |
| Landlord (Maria: Acacia, Narra) | landlord@smartrent.test | landlord123 |
| Landlord (Pedro: Molave) | landlord2@smartrent.test | landlord123 |
| Tenant | juan.dela.cruz@mail.test | tenant123 |
| Guest | guest@smartrent.test | guest123 |

Every seeded tenant uses `tenant123`. Emails are `firstname.lastname@mail.test`. The other seeded guests are `firstname.lastname@guest.test` / `guest123`.

**Roles**

| Role | Can do |
|---|---|
| Visitor (not signed in) | Browse vacant units, photos and location at `/units` (the home page) |
| Guest | Everything a visitor can, plus send an inquiry / viewing request and track it under My inquiries. Anyone can register at `/register` |
| Tenant | Own lease, rent statements and notifications. Created by their landlord from the Tenants page |
| Landlord / owner | Their **own** properties, units, unit photos, tenants, leases, billing, inquiries, analytics, reports and exports. Created by the superadmin |
| Superadmin | Runs the platform, **not** anyone's properties: Platform overview (every landlord's units, occupancy, rent roll, tenants, open inquiries) and User accounts (create landlords/superadmins, deactivate/reactivate, reset passwords, delete guests). Gets 403 on landlord pages |

**Multi-landlord data isolation.** `properties.owner_id` says which landlord owns a property; units, leases, charges, photos, inquiries and listing views belong to that landlord through it. `tenants.landlord_id` says whose tenant someone is. Every landlord route loads records through `smartrent/ownership.py`; another landlord's record answers **404** (not 403), so its existence is not revealed. Analytics take a *scope* (the landlord's property ids). Inquiries notify only the unit's landlord; the daily job notifies each tenant's own landlord.

Rules: occupied units are never shown publicly (404). Deactivating a landlord signs them out and hides their units from the public listings. Nobody can deactivate or delete themselves, and the last active superadmin cannot be deactivated. Role and active flag are re-read on every request, so a change applies at once.

Known limit: a tenant account belongs to one landlord. Someone who rents from two landlords needs two accounts (emails are unique).

**Unit photos**: Properties → a property → the *Photos* link on a unit. JPEG, PNG or WebP, checked by the file's first bytes (a renamed script is refused), saved under a random name in `storage/uploads/units/`. The first photo is the cover. The seed copies the sample photos in `smartrent/seed_photos/` (free Unsplash photos, see `CREDITS.txt`).

**Upgrading an existing database**: `python run.py init` adds the new tables and columns without touching data. `python run.py setup` re-seeds (wipes data and uploaded photos).

**Freeze the date for your defense:** set `TODAY=2026-09-11` in `.env`, then `flask seed`. Every rule and chart uses that date.

**Daily job** (`python run.py daily`): creates the month's charges, sends due-date reminders, warns about leases ending soon. Safe to run twice.
- Windows Task Scheduler: Program `C:\path\to\.venv\Scripts\python.exe`, Arguments `run.py daily`, Start in the project folder.
- Linux: `5 0 * * * cd /path/to/smartrent_py && .venv/bin/flask --app wsgi daily`
- Demo: press **Run daily tasks now** on the admin dashboard.

**Tests:** `python run.py test` (or `pytest`) (needs a running PostGIS server; it creates its own `smartrent_test` database. Override with `TEST_DATABASE_URL`).

**Production:** `gunicorn wsgi:app`, a real `SECRET_KEY`, `SESSION_COOKIE_SECURE=true` behind HTTPS.

## 3. Folder map

```
smartrent_py/
├── run.py (launcher), wsgi.py, config.py, requirements.txt, .env.example
├── database/schema.sql          the real schema (pg_dump of what the models create)
├── smartrent/
│   ├── __init__.py              create_app(): config, blueprints, error pages
│   ├── models.py                User, Property(PostGIS), Unit, Tenant, Lease, Charge, Notification
│   ├── auth_helpers.py          login_required / role_required, sidebar menu per role
│   ├── ownership.py             landlord data isolation: own_property / own_unit / own_tenant / own_lease / scope
│   ├── utils.py                 today(), month math (dateutil), Jinja filters
│   ├── cli.py                   flask init-db | reset-db | seed | daily
│   ├── routes/                  auth, admin, properties, reports, tenant, api, listings (public), superadmin
│   ├── services/                ALL business logic
│   │   ├── billing.py             monthly charges + the daily job
│   │   ├── tenant_status.py       lease-based status
│   │   ├── analytics.py           pandas KPIs, charts data, rule-based insights
│   │   ├── excel.py               openpyxl export + validated import
│   │   ├── geo.py                 OpenTopoData + Open-Meteo calls
│   │   ├── notify.py              in-app notifications + email (SMTP or log file)
│   │   ├── photos.py              unit photo upload (signature check), cover, delete
│   │   └── seed.py                demo data
│   ├── templates/               Jinja2 pages
│   └── static/                  css, js, bundled Bootstrap/Leaflet/Chart.js
├── tests/                       47 tests against real PostGIS
└── storage/logs/mail.log        emails when SMTP is off
```
Routes handle input and display. Services decide. A rule changes in one service, not ten pages.

## 4. Architecture and integration points

```
   Admin browser (Jinja pages, Bootstrap, Chart.js, Leaflet)      Tenant browser
          │   ▲ JSON / GeoJSON                                         │
          ▼   │                                                        ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │ Flask app: routes → services (billing, analytics, excel, geo, notify)│
   │            /api/analytics  /api/properties.geojson  /api/…/site      │
   └──┬───────────────┬──────────────┬───────────────┬───────────────┬───┘
      │               │              │               │               │
 PostgreSQL       flask daily     SMTP (Mailtrap)  OpenTopoData   Open-Meteo
 + PostGIS        (OS scheduler)  or mail.log      (elevation)    (weather)
```

| Integration point | Status | What it proves |
|---|---|---|
| Browser ↔ JSON API (Chart.js) | Done | Front end and back end talk through an API |
| Browser ↔ GeoJSON API (Leaflet) | Done | PostGIS geometry travels to the map as a standard format |
| Flask ↔ PostgreSQL/PostGIS | Done | ORM + spatial queries (`ST_DWithin`, `ST_AsGeoJSON`) |
| Flask ↔ OpenTopoData | Coded, tested with mocks | External REST API, cached in the database |
| Flask ↔ Open-Meteo | Coded, tested with mocks | External REST API, 10-minute cache, graceful failure |
| OS scheduler ↔ `flask daily` | Done | Time-based automation without a user clicking |
| Excel import/export | Done | File-based integration with spreadsheets |
| SMTP email | Ready | Set `MAIL_ENABLED=true` + Mailtrap credentials |

"Coded, tested with mocks" means exactly that: the two public APIs were not reachable from the build environment, so they were tested with fake responses only. **Run the property page once with internet before your defense** and check the Site conditions panel fills in.

## 5. What was removed, and what it costs

Removed: payment submission, proof upload, admin verification, receipts, the `payments` table, the `v_charges` balance view, and every metric built on payments.

Gone with it, because they cannot exist without payment data: balance, overdue, "paid late / on time", collection rate, on-time rate, outstanding amount, the "at risk" tenant status, payment reminders for unpaid rent.

What replaced them:
- Charges are **rent statements** (amount + due date). Nothing says paid or unpaid.
- Tenant status is lease-based: Active, New, Ending soon, Lease expired, No lease.
- Analytics: occupancy (now and 12-month trend), economic occupancy, rent roll, billed per month, vacancy loss, rent at risk, 6-month revenue forecast, tenure, rent spread per property, per-unit performance, leases ending per month.
- Reminders say rent is due on a date. They do not claim it is unpaid.

If your panel asks "how do you know a tenant paid?", the honest answer is: this version does not.

## 6. Business rules

| Rule | Value | Where |
|---|---|---|
| Billing | One charge per active lease per month, full month rent, no proration | `billing.generate` |
| Due date | Lease `due_day`, capped to month length (31 → 30 in September) | `billing.generate` |
| Idempotent billing | `UNIQUE (lease_id, period)` + `ON CONFLICT DO NOTHING` | `models.Charge`, `billing.generate` |
| One active lease per unit and per tenant | Partial unique indexes in PostgreSQL + row lock when creating | `models.Lease`, `admin._create_lease` |
| Unit occupied | Only through a lease. Admin cannot set it by hand | `properties._handle_post` |
| Reminder | 3 days before due date | `config.REMINDER_DAYS` |
| Lease warning | 30 days before end | `config.LEASE_WARN_DAYS` |
| Nearby radius | 2 km | `config.NEARBY_RADIUS_M` |

**Tenant status** (computed live, never stored): Lease expired (end date passed, still active) → Ending soon (≤30 days) → New (started ≤30 days ago) → Active. No active lease = No lease.

**Metric definitions**
- Rent roll = sum of monthly rent on active leases
- Billed = sum of charges for a month
- Occupancy = occupied units ÷ all units × 100 (maintenance counts as not occupied)
- Vacancy loss = monthly rent of vacant units
- Economic occupancy = rent roll ÷ (rent roll + asking rent of vacant and maintenance units) × 100
- Occupancy trend = units that got a charge that month ÷ all units today × 100 (built from billing history)
- Rent at risk = monthly rent of active leases ending within 90 days (or already past their end)
- Forecast: contracted = active leases covering that month; at risk = rent lost if leases ending before it are not renewed; vacant potential = asking rent of vacant units
- Tenure = months since an active lease started
- Unit vs property median = unit asking rent ÷ median asking rent of its property − 1
- Idle months = months since a not-occupied unit was last billed ("Never" if it has never been let)
- Listing views = visits to a unit's public page by visitors, guests and tenants (staff clicks are not counted)
- Inquiry rate = inquiries ÷ listing views × 100, last 30 days. Many views with no inquiries flags a price or photo problem

The Analytics page has a property filter (`?property=<id>`), which narrows every KPI, chart, table and the Excel export. The **Analytics pack** (Reports page, or Export on Analytics) is one workbook with a sheet each for Summary, Monthly, Forecast, Lease expiry, Tenure, Rent by property, Units and Insights.

"Rule-based insights" are IF-rules over the data. They are not AI or machine learning. Call them what they are.

## 7. Security checklist

Done:
- Passwords hashed (werkzeug), checked with constant-time compare
- Session cleared on login (no session fixation), HttpOnly + SameSite=Lax cookies
- CSRF token on every POST (Flask-WTF); logout is a POST
- SQLAlchemy parameterises every query (stops SQL injection)
- Jinja auto-escapes output; JS-built HTML escapes values
- Role check on every admin route and API route through `before_request` guards
- Tenants only ever query by their own `tenant_id` from the session
- Excel upload: `.xlsx` only, 5 MB cap, parsed read-only, never executed, all-or-nothing import
- External API calls: 6-second timeout, never crash the page

Known gaps (say them before the panel does):
- No login lockout after repeated failed attempts (only a 0.4 s delay)
- No password reset or change-password page
- No audit log
- `.xlsx` files are zip archives; a hostile one can be slow to parse. Fine for a trusted admin, not for the public
- HTTPS depends on the server, not the app

## 8. Suggested ownership

| Member | Owns | Must be able to explain |
|---|---|---|
| A | `models.py`, `services/billing.py`, `services/tenant_status.py`, `cli.py` | Idempotent billing, partial unique indexes, due-day capping, status rules |
| B | `routes/admin.py`, `services/analytics.py`, `routes/api.py`, `static/js/app.js` | Every metric definition, the JSON API, each chart |
| C | `routes/properties.py`, `services/excel.py`, `services/geo.py`, `services/notify.py` | PostGIS queries, Excel import validation, API caching and failure handling |

Every member must be able to open their files and explain them line by line.

## 9. Defense questions you will get

| Question | Answer lives in |
|---|---|
| Why PostGIS for a rental system? | `Property.location`, the nearby query in `routes/properties.py::detail`, `/api/properties.geojson` |
| What if billing runs twice? | `UNIQUE (lease_id, period)` + `ON CONFLICT DO NOTHING` |
| What if two admins lease the same unit? | Row lock (`with_for_update`) + partial unique index `uq_active_lease_unit` |
| What happens if OpenTopoData or Open-Meteo is down? | `services/geo.py` returns `None`, the panel shows a warning, the page still loads |
| Where is the integration? | Section 4 table |
| Why Flask and not Django? | Small app, we wanted to see routing, sessions and CSRF ourselves |
| Is this AI? | No. Rule-based insights. See `analytics.insights` |
| Where are payments? | Out of scope on purpose. Section 5 |
