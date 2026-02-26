"""
PretGo — Enregistrement des blueprints.
"""


def register_blueprints(app):
    """Importe et enregistre tous les blueprints auprès de l'application Flask."""
    from routes.core import bp as core_bp
    from routes.prets import bp as prets_bp
    from routes.personnes import bp as personnes_bp
    from routes.inventaire import bp as inventaire_bp
    from routes.admin import bp as admin_bp
    from routes.api import bp as api_bp
    from routes.export import bp as export_bp

    app.register_blueprint(core_bp)
    app.register_blueprint(prets_bp)
    app.register_blueprint(personnes_bp)
    app.register_blueprint(inventaire_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(export_bp)
