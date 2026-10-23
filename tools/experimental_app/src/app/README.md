# Prompt & Config Lab — SPA

Vite + React + TypeScript SPA over the FastAPI JSON API (ADR 0010). During the migration
it runs on its own dev port and proxies `/api` to FastAPI, so it never collides with the
live HTMX app served at FastAPI's `/`.

## Develop

Run the two processes side by side:

```bash
# 1. Backend (from the repo root) — serves the JSON API at http://localhost:8000/api
poetry run uvicorn web.app:app --reload --app-dir src

# 2. Frontend (from frontend/) — http://localhost:5173, proxies /api → :8000
npm install
npm run dev
```

Point the proxy at a different backend with `VITE_API_TARGET=http://host:port npm run dev`.

## Scripts

- `npm run dev` — Vite dev server with the `/api` proxy.
- `npm run build` — type-check (`tsc -b`) then production build to `dist/`.
- `npm run typecheck` — types only.
- `npm test` — Vitest (jsdom).

## Layout

```
src/
  main.tsx App.tsx        # providers (Theme, React Query, Router) + client-side routes
  api.ts   types.ts       # typed fetch client + Part-A response types (1:1 with the API)
  index.css               # Tailwind + design tokens (light & dark), semantic/status palette
  lib/      theme.tsx utils.ts
  hooks/    queries.ts     # React Query hooks, one per endpoint
  components/
    ui/                    # vendored shadcn primitives (team-owned)
    AppShell.tsx ThemeToggle.tsx states.tsx
  routes/                  # one screen per nav area (feature screens land per slice)
```
