# Prompt Web UI Tool

Flask UI for running multimodal prompts against PDF pages or raster images.

## Workflows

- **Count objects**: sends every preprocessed PNG page separately, parses each JSON response, merges counts, and writes one `result.json`.
- **Locate + draw boxes**: parses object coordinates, writes per-page JSON, draws boxes on every page, and merges all objects into `result.json`.

Each run has immutable artifacts under `data/prompt_<id>/runs/<run_id>/`:

- `images/` — preprocessed PNG pages at the selected DPI
- `page_json/` — local JSON for every page
- `annotated/` — rendered pages for the location workflow
- `result.json` — combined run result, expected values, and comparison metrics

## Run

```bash
pip install -r requirements.txt
python run.py
```

Configure `OPENROUTER_API_KEY`, `SQLALCHEMY_DATABASE_URI`, and optional `MODELS` through environment variables. DPI is selectable in the UI from 72 to 600.
