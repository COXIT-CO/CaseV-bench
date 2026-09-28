# `results_store`

Two schemas in the shared Postgres database, each its own Alembic project:

- **`experiments`** — tables defined in `models.py`, migrations in `alembic/`.
- **`service`** — tables defined in `service_models.py`, migrations in `service_alembic/`.

There is no hand-written `.sql`; every migration is generated from its models file.

## Applying a migration

```bash
export CASEV_EXPERIMENTS_DDL_DATABASE_URL='postgresql://experiments_ddl:...@.../casev_bench'
alembic -n experiments upgrade head

export CASEV_SERVICE_DATABASE_URL='postgresql://service_rw:...@.../casev_bench'
alembic -n service upgrade head
```

The `-n` flag is required. `alembic.ini` has no `[alembic]` section, so a bare `alembic upgrade
head` fails rather than picking a target.

## Changing the schema

```bash
# edit models.py, then:
alembic -n experiments revision --autogenerate -m "add the thing"

# or edit service_models.py, then:
alembic -n service revision --autogenerate -m "add the thing"
```

Each project's `env.py` filters autogenerate to its own schema, so it only ever proposes changes
to that schema's tables. Read the generated revision before committing it — autogenerate sees
shape, not intent.
