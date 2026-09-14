# `results_store` — the shared research schema

`experiments.run_results` is where every developer's benchmark numbers land, so that results
produced by different tools can be compared. This directory owns **what that table
looks like**. It does not own reading or writing it.

## What is here, and what is not

`models.py` is the single definition of the table; `alembic/versions/` is generated from it.
There is no `.sql` file.

- **`config_label` is immutable.** Free text, author-namespaced (`author/sliding_window_v1`). If
  what your flow does changes, bump the label; two different flows sharing one label is a silent
  comparison bug.
- **`tp`/`fp`/`fn`/`precision`/`recall`/`f1` are yours to supply**, and nothing checks that they
  agree with the `scorer_output` beside them. They are a convenience so the common query does
  not have to reach into JSONB; the blob is the source of truth.

## Applying a migration

**CI does it.** `apply-to-shared-store` runs `alembic -n experiments upgrade head` on every push to
`main` that touches this directory, holding the `experiments_ddl` credential from the
`shared-results-store` environment. Merging a migration deploys it.

By hand, when CI cannot:

```bash
export CASEV_EXPERIMENTS_DDL_DATABASE_URL='postgresql://experiments_ddl:...@.../casev_bench'
alembic -n experiments upgrade head
```

The `-n experiments` is required. There is no `[alembic]` section in `alembic.ini`, so a bare
`alembic upgrade head` fails rather than picking a target — the schemas on this database have
different owners, and forgetting the flag should not be a runnable command.

## Changing the schema

```bash
# edit models.py, then:
alembic -n experiments revision --autogenerate -m "add the thing"
```

Read the generated revision before committing it. `env.py` filters autogenerate to the
`experiments` schema, so the service's tables are invisible to it and can never be proposed for
deletion — but autogenerate still cannot see intent, only shape.

CI re-runs `--autogenerate` on every pull request and **fails if it produces anything**, which is
what keeps this file and the revisions from becoming two representations of one table.
