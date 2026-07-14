"""The JSON API split into one router per resource (ADR-0015).

``all_routers`` is the ordered list the app factory includes. Order matters only where two
routers could match the same path; these prefixes are disjoint, but the list is kept in a
stable, readable order (meta first, then the feature resources).
"""

from fastapi import APIRouter

from api.routers.drawings import router as drawings_router
from api.routers.ground_truth import router as ground_truth_router
from api.routers.leaderboard import router as leaderboard_router
from api.routers.meta import router as meta_router
from api.routers.models import router as models_router
from api.routers.prompts import router as prompts_router
from api.routers.results import router as results_router
from api.routers.runs import router as runs_router

all_routers: list[APIRouter] = [
    meta_router,
    leaderboard_router,
    results_router,
    runs_router,
    prompts_router,
    drawings_router,
    ground_truth_router,
    models_router,
]
