from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from core.client import GenerationParams
from core.scoring import ScorerWrapper

PROMPT_PATH = Path(__file__).parent / "prompts" / "object_location_v1.md"

DEFAULT_MAX_PX = 5000

# Model roster: slug -> max long-edge in pixels for rendered pages sent to that provider. Read by
# casev run for per-model image caps, and by the weekly benchmark workflow to build its job matrix.
MODEL_ROSTER: dict[str, int] = {
    "google/gemini-3.8-flash": DEFAULT_MAX_PX,
    "anthropic/claude-fable-5.1": DEFAULT_MAX_PX,
    "openai/gpt-6-astra": DEFAULT_MAX_PX,
}

DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_OUTPUT_TOKENS = 50000


def library_versions() -> dict[str, str]:
    return {
        "location-scorer": ScorerWrapper.scorer_version(),
    }


@dataclass(frozen=True, slots=True)
class RunConfig:
    model: str
    run_id: str
    dataset_dir: Path
    max_px: int
    provider_cap: int | None
    generation: GenerationParams
    prompt_text: str
    prompt_hash: str
    prompt_path: Path
    dataset_version: str
    library_versions: dict[str, str]
    compatibility_key: str

    @staticmethod
    def compute_compatibility_key(
        *,
        prompt_hash: str,
        dataset_version: str,
        requested_max_px: int,
        library_versions: dict[str, str],
    ) -> str:
        payload = {
            "prompt_hash": prompt_hash,
            "dataset_version": dataset_version,
            "render": {
                "requested_max_px": requested_max_px,
            },
            "library_versions": library_versions,
        }
        encoded = json.dumps(payload, sort_keys=True).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    @staticmethod
    def build(
        *,
        model: str,
        run_id: str,
        dataset_dir: Path,
        max_px: int = DEFAULT_MAX_PX,
        provider_cap: int | None = None,
        requested_max_px: int = DEFAULT_MAX_PX,
    ) -> RunConfig:
        prompt_text = PROMPT_PATH.read_text()
        prompt_hash = hashlib.sha256(prompt_text.encode()).hexdigest()
        dataset_version = dataset_dir.name
        versions = library_versions()
        return RunConfig(
            model=model,
            run_id=run_id,
            dataset_dir=dataset_dir,
            max_px=max_px,
            provider_cap=provider_cap,
            generation=GenerationParams(DEFAULT_TEMPERATURE, DEFAULT_MAX_OUTPUT_TOKENS),
            prompt_text=prompt_text,
            prompt_hash=prompt_hash,
            prompt_path=PROMPT_PATH,
            dataset_version=dataset_version,
            library_versions=versions,
            compatibility_key=RunConfig.compute_compatibility_key(
                prompt_hash=prompt_hash,
                dataset_version=dataset_version,
                requested_max_px=requested_max_px,
                library_versions=versions,
            ),
        )
