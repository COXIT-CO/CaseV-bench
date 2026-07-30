FROM python:3.11-slim

WORKDIR /app

# libgl/libglib pulled in by some PyMuPDF wheels' transitive deps at import time
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 \
    && rm -rf /var/lib/apt/lists/*

# packages/location-scorer is a sibling of casework-analyzer/ in the
# monorepo (build context is now the monorepo root -- see docker-compose.yml)
# -- copied to /packages so requirements.txt's relative editable install
# (`-e ../../packages/location-scorer`, resolved relative to /app) resolves
# inside the image exactly like it does for local (non-Docker) dev.
COPY packages/location-scorer /packages/location-scorer

COPY casework-analyzer/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY casework-analyzer/backend/app ./app

ENV CONFIG_PATH=/app/config/config.yaml \
    PROMPTS_DIR=/app/prompts \
    DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
