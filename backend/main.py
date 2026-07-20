import os
import json
import base64
import asyncio
import uuid
import shutil
from datetime import datetime, timezone
from io import BytesIO
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import List, Optional
from dotenv import load_dotenv
from pdf2image import convert_from_bytes
from openai import AsyncOpenAI
from json_repair import repair_json  # Library for repairing malformed JSON
from PIL import Image
from pydantic import BaseModel

load_dotenv()

# pdf2image renders pages through PIL, which caps decoded images at ~89.5M
# pixels as a decompression-bomb guard against untrusted external images.
# Here the images are generated internally from PDFs the user uploaded
# themselves (not fetched from the web), so the risk that guard protects
# against doesn't apply — but a large ARCH-D/E sheet at high DPI can easily
# exceed it. Raise the cap instead of leaving it silently warn/error mid-batch.
Image.MAX_IMAGE_PIXELS = 300_000_000  # ~300 MP ceiling, still bounded

app = FastAPI()
app.mount("/static", StaticFiles(directory="frontend"), name="static")

HISTORY_DIR = os.getenv("HISTORY_DIR", "history")
HISTORY_MAX_RUNS = int(os.getenv("HISTORY_MAX_RUNS", "50"))
os.makedirs(HISTORY_DIR, exist_ok=True)
app.mount("/history-files", StaticFiles(directory=HISTORY_DIR), name="history-files")

# ===============================
# History image resizing (perf)
# ===============================
# The History tab used to point both the small run-list thumbnail (rendered
# at 46x46px in the UI) and the full page-detail canvas (CSS-capped to the
# modal's width) directly at the ORIGINAL full-DPI PNG saved for that run —
# which can be many megabytes on a large architectural sheet. That meant
# opening the History tab downloaded and decoded dozens of full-resolution
# images just to show postage-stamp thumbnails, which is what caused the
# lag. This never touches meta.json, the run folder layout, or the original
# PNGs — it only adds small cached JPEG copies alongside them, generated on
# first request, so nothing about how history is stored or what's already
# saved changes or is at risk.
HISTORY_IMAGE_SIZES = {
    "thumb": (160, 60),     # small run-list thumbnail (shown at 46x46, 2-3x for retina)
    "display": (1800, 82),  # page-detail canvas (CSS-capped to the modal width)
}
HISTORY_CACHE_SUBDIR = "_cache"

client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY")
)

VALID_LABELS = {"cabinet", "countertop", "elevation", "elevation_callout"}
LABEL_TO_SUMMARY_KEY = {
    "cabinet": "cabinets",
    "countertop": "countertops",
    "elevation": "elevations",
    "elevation_callout": "elevation_callouts",
}

# Models whose vision head is natively trained on Google's grounding format
# [y_min, x_min, y_max, x_max]. The prompt asks these models for their
# native yxyx format via named fields (left/top/right/bottom), which are
# assembled into "box" already in canonical xyxy order by the model itself
# — so no coordinate swap is needed here. Kept only for token-budget tuning
# (these models also carry a mandatory internal "reasoning" pass).
MANDATORY_REASONING_SUBSTR = ("gemini-3", "gemini-2.5")
OPTIONAL_REASONING_SUBSTR = ("claude-sonnet", "claude-opus", "claude-haiku", "gpt-5")

def model_reasoning_kind(model_name: str) -> str:
    name = model_name.lower()
    if any(s in name for s in MANDATORY_REASONING_SUBSTR):
        return "mandatory"
    if any(s in name for s in OPTIONAL_REASONING_SUBSTR):
        return "optional"
    return "none"


# ===========================================================================
# Execution model
# ===========================================================================
# The old version always packed EVERY page of EVERY file into a single user
# message and ran one API call per model, one model after another.
#
# This version keeps that as the default, but makes each axis configurable
# from the frontend's "Execution Settings" modal:
#
#   model_execution_mode  ("sequential" | "parallel")
#       Whether the configured models are queried one after another or all
#       at once.
#
#   file_grouping_mode    ("single" | "split")
#       "single" -> every uploaded file's pages are combined into ONE group
#                   (this is the original behaviour).
#       "split"  -> each uploaded file becomes its OWN group, sent as a
#                   separate request (or requests) per model.
#   file_execution_mode   ("sequential" | "parallel")
#       Only meaningful when file_grouping_mode == "split". Controls whether
#       the per-file requests for a given model run one after another or
#       concurrently.
#
#   page_grouping_mode    ("single" | "split")
#       "single" -> all pages inside a file-group are sent together as one
#                   request (original behaviour).
#       "split"  -> each page inside a file-group is sent as its own,
#                   separate request.
#   page_execution_mode   ("sequential" | "parallel")
#       Only meaningful when page_grouping_mode == "split". Controls whether
#       the per-page requests inside a single file-group run one after
#       another or concurrently.
#
# Whatever combination is chosen, results from every request are merged back
# together into a single JSON response per model, so the frontend contract
# (one {model, response} entry per configured model) never changes.
#
# ===========================================================================
# Counting philosophy
# ===========================================================================
# The LLM is asked ONLY to find objects and their locations. Counting is
# never trusted from the model — "summary" in the final response is always
# computed here, in code, from the actual "objects" list. Any "summary" a
# model includes in its own raw output is parsed but discarded; only
# "objects" is ever read from a model response (see run_batch below).
#
# ===========================================================================
# Run history
# ===========================================================================
# Every /api/generate call is persisted to HISTORY_DIR/<run_id>/:
#   meta.json    - timestamp, system_prompt, per-model prompts, dpi,
#                  execution settings, file list, and per-model results
#                  (raw response + code-computed counts).
#   images/      - every rendered page, saved once (shared across models),
#                  so history shows exactly what the models were shown,
#                  independent of later DPI/viewer changes.
# The oldest runs are pruned once HISTORY_MAX_RUNS is exceeded, since
# high-DPI dense sheets can add up in disk space quickly.


@app.get("/")
async def read_index():
    return FileResponse("frontend/index.html")


def compute_counts(response_text: str) -> dict:
    """Recompute per-label counts from a model's raw response JSON, ignoring
    any 'summary' the model may have included — counting is code's job."""
    counts = {"cabinets": 0, "countertops": 0, "elevations": 0, "elevation_callouts": 0}
    try:
        data = json.loads(response_text)
        objects = data.get("objects", []) if isinstance(data, dict) else []
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            key = LABEL_TO_SUMMARY_KEY.get(obj.get("label"))
            if key:
                counts[key] += 1
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    return counts


def get_history_image_path(run_id: str, filename: str, size: str):
    """Return the path to a cached, resized JPEG copy of a saved history
    page image, generating it on first request. The original PNG saved at
    generation time is never modified or moved — this only ever adds a
    small derived file next to it. Works identically for old and new runs,
    since it derives from whatever's already on disk rather than anything
    written at save time.
    """
    preset = HISTORY_IMAGE_SIZES.get(size)
    if preset is None:
        return None

    # Guard against path traversal via a crafted filename/run_id.
    if "/" in run_id or ".." in run_id or "\\" in run_id:
        return None
    filename = os.path.basename(filename)
    if not filename or ".." in filename:
        return None

    images_dir = os.path.join(HISTORY_DIR, run_id, "images")
    src_path = os.path.join(images_dir, filename)
    if not os.path.isfile(src_path):
        return None

    cache_dir = os.path.join(images_dir, HISTORY_CACHE_SUBDIR)
    os.makedirs(cache_dir, exist_ok=True)
    base_name, _ext = os.path.splitext(filename)
    cache_path = os.path.join(cache_dir, f"{base_name}_{size}.jpg")

    # Reuse the cached copy if it's already there and not older than the
    # source (covers the source ever being replaced/re-saved).
    if os.path.isfile(cache_path) and os.path.getmtime(cache_path) >= os.path.getmtime(src_path):
        return cache_path

    max_dim, quality = preset
    with Image.open(src_path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_dim, max_dim), Image.LANCZOS)
        im.save(cache_path, format="JPEG", quality=quality, optimize=True)

    return cache_path


def prune_history():
    try:
        run_dirs = [
            d for d in os.listdir(HISTORY_DIR)
            if os.path.isdir(os.path.join(HISTORY_DIR, d))
        ]
    except FileNotFoundError:
        return
    run_dirs.sort()  # run_id is timestamp-prefixed, so lexical sort == chronological
    excess = len(run_dirs) - HISTORY_MAX_RUNS
    for old_dir in run_dirs[:max(0, excess)]:
        shutil.rmtree(os.path.join(HISTORY_DIR, old_dir), ignore_errors=True)


@app.post("/api/generate")
async def generate_responses(
        system_prompt: str = Form(...),
        models_data: str = Form(...),
        dpi: int = Form(200),
        model_execution_mode: str = Form("sequential"),
        file_grouping_mode: str = Form("single"),
        file_execution_mode: str = Form("sequential"),
        page_grouping_mode: str = Form("single"),
        page_execution_mode: str = Form("sequential"),
        files: List[UploadFile] = File(...)
):
    models = json.loads(models_data)

    # Guard against absurd or malicious values while still respecting the
    # user's chosen resolution (matches the 72-600 range exposed in the UI).
    dpi = max(72, min(600, dpi))

    def norm_mode(value, default="sequential"):
        value = (value or default).strip().lower()
        return value if value in ("sequential", "parallel") else default

    model_execution_mode = norm_mode(model_execution_mode)
    file_execution_mode = norm_mode(file_execution_mode)
    page_execution_mode = norm_mode(page_execution_mode)
    file_grouping_mode = "split" if (file_grouping_mode or "").strip().lower() == "split" else "single"
    page_grouping_mode = "split" if (page_grouping_mode or "").strip().lower() == "split" else "single"

    execution_settings = {
        "model_execution_mode": model_execution_mode,
        "file_grouping_mode": file_grouping_mode,
        "file_execution_mode": file_execution_mode,
        "page_grouping_mode": page_grouping_mode,
        "page_execution_mode": page_execution_mode,
    }

    # ===============================
    # Convert every PDF page to an image
    # ===============================
    # file_pages[file_idx] = [(page_num, base64_png, filename), ...]
    file_pages: List[List[tuple]] = []
    file_names: List[str] = []

    for f_idx, file in enumerate(files):
        await file.seek(0)
        pdf_bytes = await file.read()
        file_names.append(file.filename)

        images = convert_from_bytes(
            pdf_bytes,
            dpi=dpi,
            fmt="png"
        )

        pages = []
        for p_idx, img in enumerate(images):
            buffered = BytesIO()
            img.save(buffered, format="PNG", optimize=True)
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            pages.append((p_idx + 1, img_str, file.filename, img.width, img.height))
        file_pages.append(pages)

    total_images = sum(len(p) for p in file_pages)

    # ===============================
    # Set up this run's history folder + persist the rendered pages once,
    # shared across every model (they all see the exact same images).
    # ===============================
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    run_dir = os.path.join(HISTORY_DIR, run_id)
    images_dir = os.path.join(run_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    # (file_index, page_num) -> (width_px, height_px) of the exact image
    # sent to the models. Fallback used if a model ignores the 0-1000
    # normalization and returns raw pixel coordinates instead — see the
    # coordinate normalization step in run_batch below.
    page_pixel_dims = {}

    pages_meta = []
    for f_idx, pages in enumerate(file_pages):
        for (page_num, b64, fname, px_w, px_h) in pages:
            image_filename = f"{f_idx}_{page_num}.png"
            with open(os.path.join(images_dir, image_filename), "wb") as f:
                f.write(base64.b64decode(b64))
            pages_meta.append({
                "file_index": f_idx,
                "file_name": fname,
                "page_num": page_num,
                "image_url": f"/history-files/{run_id}/images/{image_filename}",
            })
            page_pixel_dims[(f_idx, page_num)] = (px_w, px_h)

    # Nothing to analyze — skip the model loop and say so clearly instead of
    # sending an empty request to every model.
    if total_images == 0:
        results = [
            {"model": item["model"], "response": json.dumps({"error": "No pages were found in the uploaded PDF(s)."})}
            for item in models
        ]
        save_history_meta(run_id, run_dir, system_prompt, dpi, execution_settings,
                           file_names, pages_meta, models, results)
        prune_history()
        return {"results": results, "run_id": run_id}

    # ===============================
    # Build file groups, then batches (requests) within each group
    # ===============================
    if file_grouping_mode == "split":
        file_groups = [[i] for i in range(len(file_pages))]
    else:
        file_groups = [list(range(len(file_pages)))]

    # file_group_batches[g] = list of batches; a batch is a list of
    # (file_index, page_num, base64_png, filename) tuples that will be sent
    # together in ONE request.
    file_group_batches: List[List[list]] = []
    for group in file_groups:
        entries = [
            (fidx, p, b64, fname)
            for fidx in group
            for (p, b64, fname, _px_w, _px_h) in file_pages[fidx]
        ]
        if page_grouping_mode == "split":
            batches = [[entry] for entry in entries]
        else:
            batches = [entries]
        file_group_batches.append(batches)

    total_requests_per_model = sum(len(b) for b in file_group_batches)
    print(f"[generate] run={run_id} {total_images} page(s) at {dpi} DPI split into "
          f"{len(file_group_batches)} file-group(s) / {total_requests_per_model} request(s) per model "
          f"(models: {model_execution_mode}, files: {file_grouping_mode}/{file_execution_mode}, "
          f"pages: {page_grouping_mode}/{page_execution_mode}).")

    # ===============================
    # Run a single batch (one API call) for one model
    # ===============================
    async def run_batch(model_name: str, user_prompt: str, entries: list):
        n = len(entries)
        content = [{"type": "text", "text": user_prompt}]
        local_mapping = []

        for local_idx, (fidx, page_num, b64, fname) in enumerate(entries):
            content.append({
                "type": "text",
                "text": f"\n--- START OF IMAGE {local_idx} (File: {fname}, Page: {page_num}) ---\n"
            })
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"}
            })
            content.append({
                "type": "text",
                "text": f"\n--- END OF IMAGE {local_idx} ---\n"
            })
            local_mapping.append({"file_index": fidx, "page_num": page_num})

        if n == 1:
            override_text = """
                    [SYSTEM OVERRIDE - CRITICAL]
                    You have been provided with EXACTLY 1 image in this request.
                    For every object found, its "image_index" MUST be 0.
                    """
        else:
            override_text = f"""
                    [SYSTEM OVERRIDE - CRITICAL]
                    You have been provided with EXACTLY {n} images, all in this single request.
                    They are marked sequentially from IMAGE 0 to IMAGE {n - 1}.
                    You MUST process EACH image separately.
                    DO NOT group all objects into "image_index": 0.
                    If an object is found in IMAGE 2, its "image_index" MUST be 2.
                    """

        final_system_instruction = system_prompt + override_text

        reasoning_kind = model_reasoning_kind(model_name)

        # Output grows with OBJECT COUNT on the page, not page count in the
        # batch — with page_grouping_mode=split, n is almost always 1, so a
        # formula that only scales with n stays flat regardless of sheet
        # density. The detailed left/top/right/bottom+box schema is verbose
        # per object, so even non-reasoning models need real headroom on a
        # dense sheet, not just the old flat ~4-5k tokens.
        base_tokens = 12000 if reasoning_kind == "mandatory" else 8000
        max_response_tokens = min(32000, base_tokens + n * 2000)

        extra_body = {}
        if reasoning_kind == "mandatory":
            # Cannot fully disable thinking on these models, but "low"
            # effort leaves much more of max_tokens for the actual JSON
            # instead of being consumed by internal reasoning.
            extra_body["reasoning"] = {"effort": "low"}
        elif reasoning_kind == "optional":
            # Off by default, but can silently switch on and consume the
            # ENTIRE max_tokens budget before writing any visible content
            # — observed as finish_reason "length" with empty content.
            # Force it off explicitly rather than relying on the default.
            extra_body["reasoning"] = {"enabled": False}

        messages = [
            {"role": "system", "content": final_system_instruction},
            {"role": "user", "content": content}
        ]

        try:
            response = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=0,
                top_p=0.1,
                max_tokens=max_response_tokens,
                extra_body=extra_body
            )

            if not response.choices:
                raise Exception("Model returned no choices")

            message = response.choices[0].message
            if message is None:
                raise Exception("Model returned empty message")

            response_text = message.content
            if not response_text:
                raise Exception(f"Model returned empty content. Finish reason: {response.choices[0].finish_reason}")

            if response.choices[0].finish_reason == "length":
                print(f"[{model_name}] WARNING: response was truncated at {max_response_tokens} tokens; "
                      f"some pages/objects may be missing from this batch.")

            response_text = response_text.replace("```json", "").replace("```", "").strip()

            try:
                parsed_json = json.loads(response_text)
            except json.JSONDecodeError:
                print(f"[{model_name}] Invalid JSON. Attempting repair...")
                parsed_json = json.loads(repair_json(response_text))

            # Some models occasionally ignore the {"objects":[...]} wrapper
            # and return a bare array instead. Normalize rather than crash.
            if isinstance(parsed_json, list):
                parsed_json = {"objects": parsed_json}
            elif not isinstance(parsed_json, dict):
                raise Exception(
                    f"Unexpected top-level JSON type from model: {type(parsed_json).__name__}"
                )

            raw_objects = parsed_json.get("objects", [])
            if not isinstance(raw_objects, list):
                raw_objects = []

            valid_objects = []
            for obj in raw_objects:
                # Defensive: skip any entry that isn't itself an object
                # (a stray nested list or string slipped in by the model).
                if not isinstance(obj, dict):
                    continue

                label = obj.get("label")
                if label not in VALID_LABELS:
                    continue

                box = obj.get("box")
                if not (isinstance(box, list) and len(box) == 4
                        and all(isinstance(v, (int, float)) for v in box)):
                    continue  # malformed box — skip rather than pass garbage downstream

                img_idx = obj.get("image_index")
                if isinstance(img_idx, int) and 0 <= img_idx < n:
                    obj["file_index"] = local_mapping[img_idx]["file_index"]
                    obj["page_num"] = local_mapping[img_idx]["page_num"]

                obj.pop("image_index", None)

                # Some models (observed repeatedly with claude-sonnet-5)
                # ignore the 0-1000 normalization instruction and return raw
                # pixel coordinates of the exact image they were sent,
                # regardless of prompt wording. A value above 1000 can never
                # be a valid 0-1000 coordinate — detect that here and rescale
                # using the REAL pixel size of the exact page this object
                # came from, so every response leaving this endpoint is
                # already normalized 0-1000, independent of whether the
                # model itself normalized correctly. No model-name check —
                # this only ever activates when the numbers are actually out
                # of range, so models that already normalize correctly
                # (Gemini, Qwen, ...) are completely unaffected.
                box_out = box
                if max(box) > 1000:
                    dims = page_pixel_dims.get((obj.get("file_index"), obj.get("page_num")))
                    if dims:
                        px_w, px_h = dims
                        x0, y0, x1, y1 = box
                        box_out = [
                            round((x0 / px_w) * 1000),
                            round((y0 / px_h) * 1000),
                            round((x1 / px_w) * 1000),
                            round((y1 / px_h) * 1000),
                        ]
                    box_out = [max(0, min(1000, v)) for v in box_out]

                # Keep only what the frontend actually needs — label, box,
                # file_index, page_num. Any left/top/right/bottom fields the
                # model was asked to compute were scaffolding for building
                # "box" correctly and aren't needed downstream.
                valid_objects.append({
                    "label": obj["label"],
                    "box": box_out,
                    "file_index": obj.get("file_index"),
                    "page_num": obj.get("page_num"),
                })

            return {"ok": True, "objects": valid_objects}

        except Exception as e:
            print(f"Error from model {model_name} on a batch: {e}")
            return {"ok": False, "error": str(e)}

    # ===============================
    # Run every batch for one model, respecting file/page execution modes
    # ===============================
    async def run_batches_in_group(model_name: str, user_prompt: str, batches: list):
        if page_execution_mode == "parallel":
            return await asyncio.gather(*[run_batch(model_name, user_prompt, b) for b in batches])
        results = []
        for b in batches:
            results.append(await run_batch(model_name, user_prompt, b))
        return results

    async def run_model(item: dict):
        model_name = item["model"]
        user_prompt = item["prompt"]

        if file_execution_mode == "parallel":
            group_results = await asyncio.gather(*[
                run_batches_in_group(model_name, user_prompt, batches)
                for batches in file_group_batches
            ])
        else:
            group_results = []
            for batches in file_group_batches:
                group_results.append(await run_batches_in_group(model_name, user_prompt, batches))

        all_objects = []
        errors = []
        for batch_results in group_results:
            for r in batch_results:
                if r["ok"]:
                    all_objects.extend(r["objects"])
                else:
                    errors.append(r["error"])

        # If every single batch failed, surface that clearly instead of
        # returning an empty-but-successful-looking payload.
        if errors and not all_objects and len(errors) == total_requests_per_model:
            return {"model": model_name, "response": json.dumps({"error": "; ".join(errors)}, ensure_ascii=False)}

        # Counting is ALWAYS done here, from the actual objects list — never
        # trusted from the model's own output.
        summary = {"cabinets": 0, "countertops": 0, "elevations": 0, "elevation_callouts": 0}
        for obj in all_objects:
            key = LABEL_TO_SUMMARY_KEY.get(obj.get("label"))
            if key:
                summary[key] += 1

        final_json = {"summary": summary, "objects": all_objects}
        if errors:
            final_json["errors"] = errors

        return {"model": model_name, "response": json.dumps(final_json, ensure_ascii=False)}

    # ===============================
    # Run every model, respecting model_execution_mode
    # ===============================
    if model_execution_mode == "parallel":
        results = await asyncio.gather(*[run_model(item) for item in models])
    else:
        results = []
        for item in models:
            results.append(await run_model(item))

    results = list(results)
    save_history_meta(run_id, run_dir, system_prompt, dpi, execution_settings,
                       file_names, pages_meta, models, results)
    prune_history()

    return {"results": results, "run_id": run_id}


def save_history_meta(run_id, run_dir, system_prompt, dpi, execution_settings,
                       file_names, pages_meta, models, results):
    """Persist everything needed to fully reconstruct this run later: the
    prompts used (system + per-model), settings, file/page list, and every
    model's raw response alongside code-computed counts."""
    results_by_model = {r["model"]: r["response"] for r in results}

    meta = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "system_prompt": system_prompt,
        "dpi": dpi,
        "execution_settings": execution_settings,
        "files": file_names,
        "pages": pages_meta,
        "results": [
            {
                "model": item["model"],
                "prompt": item["prompt"],
                "response": results_by_model.get(item["model"], ""),
                "counts": compute_counts(results_by_model.get(item["model"], "")),
                # Filled in later via PUT /api/history/{run_id}/expected-summary,
                # once a human pastes in the ground-truth counts for this run —
                # kept here (not a separate file) so it travels with the run and
                # survives export/prune the same way everything else does.
                "expected_summary": None,
            }
            for item in models
        ],
    }

    with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


# ===============================
# History API
# ===============================

@app.get("/api/history-image/{run_id}/{filename}")
async def get_history_image(run_id: str, filename: str, size: str = "display"):
    """Serves a small, cached JPEG copy of a saved history page image,
    generated on first request from the original PNG. Used instead of the
    raw /history-files/... original for thumbnails and the page-detail
    canvas, since those are shown far smaller than the original render."""
    path = get_history_image_path(run_id, filename, size)
    if not path:
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/history")
async def list_history():
    """Lightweight list for the history browser — omits full page lists and
    full raw responses to keep this fast even with many past runs."""
    if not os.path.isdir(HISTORY_DIR):
        return {"runs": []}

    run_ids = sorted(
        (d for d in os.listdir(HISTORY_DIR) if os.path.isdir(os.path.join(HISTORY_DIR, d))),
        reverse=True,  # newest first
    )

    runs = []
    for run_id in run_ids:
        meta_path = os.path.join(HISTORY_DIR, run_id, "meta.json")
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        first_page = (meta.get("pages") or [{}])[0]
        first_image_url = first_page.get("image_url")
        thumbnail_url = None
        if first_image_url:
            thumb_filename = os.path.basename(first_image_url)
            thumbnail_url = f"/api/history-image/{run_id}/{thumb_filename}?size=thumb"

        runs.append({
            "run_id": meta.get("run_id", run_id),
            "created_at": meta.get("created_at"),
            "dpi": meta.get("dpi"),
            "execution_settings": meta.get("execution_settings"),
            "files": meta.get("files", []),
            "page_count": len(meta.get("pages", [])),
            "thumbnail_url": thumbnail_url,
            "models": [
                {"model": r["model"], "counts": r.get("counts")}
                for r in meta.get("results", [])
            ],
        })

    return {"runs": runs}


@app.get("/api/history/{run_id}")
async def get_history_run(run_id: str):
    """Full detail for one run — prompts, settings, every page image URL,
    and every model's raw response, for the history detail view."""
    # Guard against path traversal via a crafted run_id.
    if "/" in run_id or ".." in run_id:
        raise HTTPException(status_code=400, detail="Invalid run_id")

    meta_path = os.path.join(HISTORY_DIR, run_id, "meta.json")
    if not os.path.isfile(meta_path):
        raise HTTPException(status_code=404, detail="Run not found")

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    # Add a resized "display" URL alongside each page's original image_url,
    # computed here rather than stored, so meta.json on disk is untouched.
    for page in meta.get("pages", []):
        original_url = page.get("image_url")
        if original_url:
            page_filename = os.path.basename(original_url)
            page["display_image_url"] = f"/api/history-image/{run_id}/{page_filename}?size=display"

    return meta


class ExpectedSummaryUpdate(BaseModel):
    model: str
    expected_summary: dict


@app.put("/api/history/{run_id}/expected-summary")
async def set_expected_summary(run_id: str, payload: ExpectedSummaryUpdate):
    """Persists a human-pasted ground-truth summary (counts per label) for
    one model's result within a run, for later accuracy evaluation. Stored
    directly in that run's meta.json, alongside everything else already
    saved for it — nothing about the existing history format changes,
    this only ever adds/overwrites the "expected_summary" key already
    reserved on each result entry."""
    if "/" in run_id or ".." in run_id:
        raise HTTPException(status_code=400, detail="Invalid run_id")

    meta_path = os.path.join(HISTORY_DIR, run_id, "meta.json")
    if not os.path.isfile(meta_path):
        raise HTTPException(status_code=404, detail="Run not found")

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    target = next((r for r in meta.get("results", []) if r.get("model") == payload.model), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Model not found in this run")

    target["expected_summary"] = payload.expected_summary

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return {"ok": True, "model": payload.model, "expected_summary": payload.expected_summary}


@app.delete("/api/history/{run_id}")
async def delete_history_run(run_id: str):
    if "/" in run_id or ".." in run_id:
        raise HTTPException(status_code=400, detail="Invalid run_id")

    run_dir = os.path.join(HISTORY_DIR, run_id)
    if not os.path.isdir(run_dir):
        raise HTTPException(status_code=404, detail="Run not found")

    shutil.rmtree(run_dir, ignore_errors=True)
    return {"deleted": run_id}