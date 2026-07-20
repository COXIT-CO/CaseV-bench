# CaseV-Bench

Benchmark tool for comparing multiple vision-language models on architectural
millwork drawing detection — cabinets, countertops, elevations, and
elevation callouts — extracted from uploaded PDF drawing sets.

## Requirements

- Docker + Docker Compose
- An [OpenRouter](https://openrouter.ai) API key

## Setup

1. Copy the example env file and fill in your key:

   ```bash
   cp .env.example .env
   ```

   ```
   OPENROUTER_API_KEY=your-key-here
   ```

2. Build and start the app (first run only):

   ```bash
   docker-compose up --build
   ```

3. On every run after that, you don't need `--build` again:

   ```bash
   docker-compose up
   ```

4. Open the app at **http://localhost:8000**

## Using it

- Drag & drop one or more PDF drawing sets into the viewer.
- Configure 1-3 models (model ID + prompt each), the shared system prompt,
  and the render DPI.
- Use **Execution Settings** to control how requests are batched — all
  files/pages in one request, or split per file/page, sequentially or in
  parallel.
- Hit **Run Benchmark** to send the drawings to every configured model and
  compare results side by side, with bounding-box overlays on the original
  pages.
- Every run is saved to the **History** tab automatically — browse past
  runs, replay a run's exact settings back into the form, or paste in an
  expected/ground-truth summary to score a model's accuracy.

## Project structure

```
backend/     FastAPI service — PDF rendering, model calls, history storage
frontend/    Static UI (vanilla HTML/JS/CSS)
history/     Runtime-generated: saved runs (images + metadata), not tracked in git
```