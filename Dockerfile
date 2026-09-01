FROM python:3.11-slim
WORKDIR /app
# `src` on the path so `results_store.models` imports the same way it does for
# alembic (see alembic.ini's prepend_sys_path) — the repo's one import root
# for that shared package.
ENV PYTHONPATH=/app/src

RUN apt-get update && apt-get install -y poppler-utils git && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]