from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

db = SQLAlchemy()
migrate = Migrate()


def create_app(config_object="config.Config"):
    app = Flask(__name__)
    app.config.from_object(config_object)

    db.init_app(app)
    migrate.init_app(app, db)

    from app.routes.dashboard import dashboard_bp
    from app.routes.trades import trades_bp
    from app.routes.journal import journal_bp
    from app.routes.imports import imports_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(trades_bp)
    app.register_blueprint(journal_bp)
    app.register_blueprint(imports_bp)

    # Start background scheduler
    from app.services.scheduler import start_scheduler
    start_scheduler(app)

    return app
