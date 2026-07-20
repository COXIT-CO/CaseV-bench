from os import getenv
from datetime import timedelta
import secrets
from dotenv import load_dotenv
from app.enums import DefaultModels

from flask import current_app


load_dotenv()


DEBUG = False

SQLALCHEMY_DATABASE_URI = getenv('SQLALCHEMY_DATABASE_URI')
SQLALCHEMY_ENGINE_OPTIONS = {
    "pool_pre_ping": True,
    "pool_recycle": 300
    }

LLM_MODELS = getenv('MODELS', '')

SECRET_KEY = secrets.token_hex(32)
PERMANENT_SESSION_LIFETIME = timedelta(hours=1)


def inject_available_models():
    if LLM_MODELS:
        models = [model.strip() for model in LLM_MODELS.split(',')]
    else:
        models = [model.value for model in DefaultModels]
        
    return models

AVAILABLE_MODELS = inject_available_models()
