import os

from flask import Flask, render_template

from config import Config

from . import cli, utils
from .extensions import csrf, db


def create_app(config_object=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)
    os.makedirs(os.path.dirname(app.config["MAIL_LOG"]), exist_ok=True)

    if app.config["SECRET_KEY"] == "dev-only-change-me" and not app.testing:
        app.logger.warning("SECRET_KEY is the development default. Set a long random SECRET_KEY in .env before real use.")

    db.init_app(app)
    csrf.init_app(app)
    utils.register_filters(app)
    cli.register(app)

    from .routes import admin, api, auth, listings, properties, reports, superadmin, tenant

    for module in (auth, listings, admin, properties, reports, tenant, api, superadmin):
        app.register_blueprint(module.bp)

    @app.context_processor
    def inject_globals():
        from .auth_helpers import current_user, nav_for

        user = current_user()
        return {"app_name": app.config["APP_NAME"], "me": user, "nav": nav_for(user), "today": utils.today()}

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("error.html", code=403, message="You do not have access to this page."), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("error.html", code=404, message="Page not found."), 404

    @app.errorhandler(413)
    def too_large(_e):
        return render_template("error.html", code=413, message="That file is too large (5 MB limit)."), 413

    return app
