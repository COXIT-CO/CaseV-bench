"""Misc endpoints: health check, frontend bootstrap config, saved prompt,
runtime-added models."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.models.schemas import (
    AddModelRequest,
    ConfigResponse,
    ModelOption,
    SavePromptRequest,
)

router = APIRouter(tags=["meta"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/config", response_model=ConfigResponse)
def get_config(settings: Settings = Depends(get_settings)) -> ConfigResponse:
    return ConfigResponse(
        models=[
            ModelOption(
                id=m.id,
                label=m.label,
                provider=m.provider,
                implemented=m.implemented,
                custom=m.custom,
            )
            for m in settings.all_models()
        ],
        default_model=settings.default_model,
        default_temperature=settings.default_temperature,
        default_max_tokens=settings.default_max_tokens,
        default_system_prompt=settings.default_system_prompt_text(),
        default_user_prompt=settings.default_user_prompt_text(),
        default_crop_prompt=settings.ai_crop_cell_prompt_text(),
        categories=settings.categories,
        pdf_dpi=settings.pdf_dpi,
    )


@router.post("/models", response_model=ModelOption)
def add_model(
    request: AddModelRequest, settings: Settings = Depends(get_settings)
) -> ModelOption:
    return settings.add_custom_model(request.id.strip())


@router.delete("/models")
def remove_model(
    model_id: str, settings: Settings = Depends(get_settings)
) -> dict[str, str]:
    settings.remove_custom_model(model_id)
    return {"removed": model_id}


@router.put("/prompt")
def save_prompt(
    request: SavePromptRequest, settings: Settings = Depends(get_settings)
) -> dict[str, str]:
    settings.save_custom_system_prompt(request.system_prompt)
    settings.save_custom_user_prompt(request.user_prompt)
    return {
        "default_system_prompt": request.system_prompt,
        "default_user_prompt": request.user_prompt,
    }
