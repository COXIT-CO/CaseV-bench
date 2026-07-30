"""Run the LLM analysis over selected pages, concurrently, with per-page results."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import Settings, get_settings
from app.models.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    PageResult,
    PageResultStatus,
    ResetResultsRequest,
)
from app.services.ai_crop import analyze_page_ai_crop
from app.services.history_store import save_run
from app.services.image_utils import downscale_image
from app.services.llm_client import LLMError, get_provider
from app.services.parser import parse_model_response, relabel_for_category
from app.services.session_store import session_store
from app.services.tiling import analyze_page_tiled

router = APIRouter(tags=["analyze"])


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_pages(
    request: AnalyzeRequest,
    settings: Settings = Depends(get_settings),
) -> AnalyzeResponse:
    session = session_store.get(request.session_id)
    if session is None:
        raise HTTPException(
            status_code=404, detail="Unknown session. Upload the PDF again."
        )

    model_option = settings.find_model(request.model)
    if model_option is None:
        raise HTTPException(status_code=400, detail=f"Unknown model: {request.model}")

    unknown_pages = [pid for pid in request.page_ids if pid not in session.pages]
    if unknown_pages:
        raise HTTPException(
            status_code=400, detail=f"Unknown page id(s): {unknown_pages}"
        )

    try:
        provider = get_provider(model_option.provider, settings)
    except LLMError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Recorded once per request (not per page): every page in this request
    # shares the same prompts/model/temperature/dpi, which is exactly the
    # configuration a user would want to reload from the History view.
    save_run(
        settings,
        {
            "filename": session.filename,
            "model": request.model,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "dpi": session.dpi,
            "system_prompt": request.system_prompt,
            "user_prompt": request.user_prompt,
            "reference_image_names": [
                ref.name or f"image {i + 1}"
                for i, ref in enumerate(request.reference_images)
            ],
            "page_count": len(request.page_ids),
        },
    )

    semaphore = asyncio.Semaphore(settings.concurrency_limit)

    async def analyze_one(page_id: str) -> PageResult:
        page = session.pages[page_id]
        async with semaphore:
            try:
                # "Cutting" mode (request.cutting), AI-crop mode
                # (request.ai_crop -- mutually exclusive with cutting, see
                # AnalyzeRequest's model_validator), and Multi-Prompting mode
                # (request.category) are independent, orthogonal fields on
                # AnalyzeRequest and compose with zero special-casing: every
                # branch below converges on a plain (raw_response, parsed,
                # parse_error, truncated, reasoning_tokens, completion_tokens,
                # crops, ai_crop_message) tuple, and relabel_for_category runs
                # identically afterward regardless of which branch produced
                # it. Note this means a Multi-Prompting run with Cutting (or
                # AI-crop) on fires N independent tiled/two-pass requests --
                # one per active category -- not one combined request across
                # categories.
                crops = None
                ai_crop_message = None
                if request.ai_crop:
                    ai_crop_result = await analyze_page_ai_crop(
                        provider=provider,
                        image_path=page.image_path,
                        system_prompt=request.system_prompt,
                        user_prompt=request.user_prompt,
                        # request.crop_prompt is the UI's editable "Crop
                        # prompt" field (PromptEditor.jsx, AI-crop only) --
                        # falls back to the same fixed default Pass 1 always
                        # used before this field existed, for a direct API
                        # call that omits it (or sends it blank).
                        cell_prompt=request.crop_prompt or settings.ai_crop_cell_prompt_text(),
                        model=request.model,
                        temperature=request.temperature,
                        max_tokens=request.max_tokens,
                        reference_images=request.reference_images,
                    )
                    raw_response = ai_crop_result.raw_text
                    parsed, parse_error = ai_crop_result.parsed, ai_crop_result.parse_error
                    truncated = ai_crop_result.truncated
                    reasoning_tokens = ai_crop_result.reasoning_tokens
                    completion_tokens = ai_crop_result.completion_tokens
                    crops = ai_crop_result.crops or None
                    ai_crop_message = ai_crop_result.no_cells_message
                elif request.cutting:
                    tiled = await analyze_page_tiled(
                        provider=provider,
                        image_path=page.image_path,
                        system_prompt=request.system_prompt,
                        user_prompt=request.user_prompt,
                        model=request.model,
                        temperature=request.temperature,
                        max_tokens=request.max_tokens,
                        grid_rows=request.grid_rows,
                        grid_cols=request.grid_cols,
                        overlap_pct=request.overlap_pct,
                        reference_images=request.reference_images,
                    )
                    raw_response = tiled.raw_text
                    parsed, parse_error = tiled.parsed, tiled.parse_error
                    truncated = tiled.truncated
                    reasoning_tokens = tiled.reasoning_tokens
                    completion_tokens = tiled.completion_tokens
                else:
                    image_bytes, media_type = downscale_image(
                        page.image_path.read_bytes(), "image/png"
                    )
                    analysis = await provider.analyze_image(
                        image_bytes=image_bytes,
                        media_type=media_type,
                        system_prompt=request.system_prompt,
                        user_prompt=request.user_prompt,
                        model=request.model,
                        temperature=request.temperature,
                        max_tokens=request.max_tokens,
                        reference_images=request.reference_images,
                    )
                    raw_response = analysis.text
                    parsed, parse_error = parse_model_response(analysis.text)
                    truncated = analysis.truncated
                    reasoning_tokens = analysis.reasoning_tokens
                    completion_tokens = analysis.completion_tokens

                if request.category and parsed is not None:
                    parsed = relabel_for_category(parsed, request.category)
                result = PageResult(
                    page_id=page_id,
                    page_number=page.page_number,
                    status=PageResultStatus.DONE,
                    raw_response=raw_response,
                    parsed=parsed,
                    parse_error=parse_error,
                    truncated=truncated,
                    reasoning_tokens=reasoning_tokens,
                    completion_tokens=completion_tokens,
                    category=request.category,
                    crops=crops,
                    ai_crop_message=ai_crop_message,
                )
            except LLMError as exc:
                result = PageResult(
                    page_id=page_id,
                    page_number=page.page_number,
                    status=PageResultStatus.ERROR,
                    error_message=str(exc),
                    category=request.category,
                )
            # Multi-Prompting mode (request.category set) merges into
            # whatever other categories have already stored for this page
            # instead of overwriting -- see session_store.set_result and
            # parser.merge_page_results.
            session_store.set_result(
                request.session_id, result, merge=bool(request.category)
            )
            return result

    results = await asyncio.gather(*(analyze_one(pid) for pid in request.page_ids))
    results = sorted(results, key=lambda r: r.page_number)

    return AnalyzeResponse(
        session_id=request.session_id, model=request.model, results=results
    )


@router.post("/sessions/{session_id}/reset-results")
def reset_results(session_id: str, request: ResetResultsRequest) -> dict[str, str]:
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session.")
    session_store.clear_results(session_id, request.page_ids)
    return {"status": "ok"}
