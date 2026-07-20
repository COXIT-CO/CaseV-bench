from flask import Flask
from sqlalchemy import inspect, text

import app.config
from app.extensions import db, migrate

__all__ = ['create_app']


def _ensure_legacy_schema(app: Flask) -> None:
    """Keep the bundled SQLite database usable without forcing a manual migration."""
    additions = {
        'prompts': {
            'system_prompt': "TEXT NOT NULL DEFAULT ''",
            'expected_json': 'TEXT',
        },
        'prompt_runs': {
            'workflow': "VARCHAR(32) NOT NULL DEFAULT 'count'",
            'dpi': 'INTEGER NOT NULL DEFAULT 200',
            'result_json': 'TEXT',
            'artifacts_path': 'VARCHAR(512)',
        },
    }
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    with db.engine.begin() as connection:
        for table, columns in additions.items():
            if table not in existing_tables:
                continue
            existing = {column['name'] for column in inspector.get_columns(table)}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {definition}'))


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

    with flask_app.app_context():
        db.create_all()
        _ensure_legacy_schema(flask_app)

    return flask_app
