\
import os
import sys

from smartrent import create_app
from smartrent.cli import init_database
from smartrent.services import billing, seed

app = create_app()


def main(cmd: str) -> int:
    if cmd == "run":
        host = os.getenv("HOST", "127.0.0.1")
        port = int(os.getenv("PORT", "5000"))
        app.run(host=host, port=port, debug=os.getenv("DEBUG", "true").lower() == "true")
        return 0
    if cmd == "test":
        import pytest
        return pytest.main(["-q"])
    with app.app_context():
        if cmd in ("setup", "init"):
            init_database()
            print("PostGIS enabled, tables ready.")
        if cmd in ("setup", "seed"):
            print("Seeded:", seed.run())
            print("Superadmin:   superadmin@smartrent.test / super123")
            print("Landlords:    landlord@smartrent.test / landlord123, landlord2@smartrent.test / landlord123")
            print("Tenant login: juan.dela.cruz@mail.test / tenant123")
            print("Guest login:  guest@smartrent.test / guest123")
        if cmd == "daily":
            r = billing.daily_tasks()
            print(f"charges:{r['charges']} reminders:{r['reminders']} leases:{r['leases']}")
        if cmd not in ("setup", "init", "seed", "daily"):
            print(__doc__)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "run"))
