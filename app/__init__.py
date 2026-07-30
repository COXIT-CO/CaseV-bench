from flask import Flask

import app.config
from app.extensions import db, migrate

__all__ = ['create_app']


def create_app() -> Flask:
    flask_app = Flask(__name__, template_folder='templates')
    flask_app.config.from_pyfile('config.py')

    @flask_app.context_processor
    def inject_available_models():
        return dict(available_models=flask_app.config['AVAILABLE_MODELS'])

    db.init_app(flask_app)
    migrate.init_app(flask_app, db)

    from app.openrouter import OpenRouterClient
    flask_app.extensions['openrouter_client'] = OpenRouterClient()

    from app.routes import routes
    flask_app.register_blueprint(routes)

    return flask_app
