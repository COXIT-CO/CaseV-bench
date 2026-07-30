"""Application configuration: environment variables + config.yaml.

Env vars (secrets, deployment paths) and config.yaml (models, prompts,
defaults, tunables) are kept separate on purpose: config.yaml is meant to be
edited freely without touching code or redeploying secrets.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_THIS_FILE = Path(__file__).resolve()
BACKEND_DIR = _THIS_FILE.parents[2]
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
DEFAULT_PROMPTS_DIR = PROJECT_ROOT / "prompts"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


class ModelOption(BaseModel):
    id: str
    label: str
    provider: str = "openrouter"
    # False marks a model as a selectable-but-not-wired-up placeholder: its
    # provider has no factory in `_PROVIDER_FACTORIES` (llm_client.py), so
    # `get_provider` raises a clean "Unknown LLM provider" error at analyze
    # time rather than silently pretending to call a model there's no
    # adapter/API key/request format for yet.
    implemented: bool = True
    # True for a model the user added at runtime via POST /api/models (see
    # Settings.add_custom_model), as opposed to one declared in config.yaml.
    # Only custom models can be removed via DELETE /api/models.
    custom: bool = False


class Env(BaseSettings):
    """Values that must come from the environment, never from config.yaml."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_site_url: str = Field(default="", alias="OPENROUTER_SITE_URL")
    openrouter_app_name: str = Field(default="", alias="OPENROUTER_APP_NAME")
    config_path: str = Field(default=str(DEFAULT_CONFIG_PATH), alias="CONFIG_PATH")
    prompts_dir: str = Field(default=str(DEFAULT_PROMPTS_DIR), alias="PROMPTS_DIR")
    data_dir: str = Field(default=str(DEFAULT_DATA_DIR), alias="DATA_DIR")
    cors_origins: str = Field(
        default="http://localhost:5173", alias="CORS_ORIGINS"
    )


class Settings:
    """Merged runtime settings: env vars + config.yaml, loaded once per process."""

    def __init__(self) -> None:
        self.env = Env()

        raw = self._load_yaml(Path(self.env.config_path))

        self.app_name: str = raw.get("app", {}).get(
            "name", "Casework Drawing Analyzer"
        )
        self.models: list[ModelOption] = [
            ModelOption(**m) for m in raw.get("models", [])
        ]

        defaults = raw.get("defaults", {})
        self.default_model: str = defaults.get(
            "model", self.models[0].id if self.models else ""
        )
        self.default_temperature: float = float(defaults.get("temperature", 0.0))
        self.default_max_tokens: int = int(defaults.get("max_tokens", 4096))
        self.default_system_prompt_file: str = defaults.get(
            "system_prompt_file", "default.txt"
        )
        self.default_user_prompt_file: str = defaults.get(
            "user_prompt_file", "default_user.txt"
        )
        # AI-crop mode's Pass 1 (see services/ai_crop.py) always uses this
        # fixed, internal cell-detection prompt, regardless of whatever
        # system_prompt/user_prompt the request actually carries for its
        # real object-detection task (that's Pass 2's job) -- not
        # user-editable via the Prompt Editor UI, so unlike
        # default_system_prompt_file/default_user_prompt_file there's no
        # custom-override file layered on top of this.
        self.ai_crop_cell_prompt_file: str = defaults.get(
            "ai_crop_cell_prompt_file", "elevations_unfiltered.txt"
        )

        pdf_cfg = raw.get("pdf", {})
        self.pdf_dpi: int = int(pdf_cfg.get("dpi", 150))
        self.max_file_size_mb: int = int(pdf_cfg.get("max_file_size_mb", 100))

        analysis_cfg = raw.get("analysis", {})
        self.concurrency_limit: int = int(analysis_cfg.get("concurrency_limit", 4))
        self.max_retries: int = int(analysis_cfg.get("max_retries", 5))
        self.retry_base_delay_seconds: float = float(
            analysis_cfg.get("retry_base_delay_seconds", 1.0)
        )

        self.categories: list[str] = raw.get(
            "categories",
            ["cabinets", "elevations", "countertops", "elevation_callouts"],
        )

        self.data_dir = Path(self.env.data_dir)
        self.prompts_dir = Path(self.env.prompts_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.openrouter_api_key = self.env.openrouter_api_key
        self.openrouter_site_url = self.env.openrouter_site_url
        self.openrouter_app_name = self.env.openrouter_app_name
        self.cors_origins = [
            o.strip() for o in self.env.cors_origins.split(",") if o.strip()
        ]

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @property
    def custom_system_prompt_path(self) -> Path:
        # Saved under data_dir, not prompts_dir: prompts_dir is mounted
        # read-only in docker-compose.yml (it's meant to be edited by
        # redeploying, not from the UI), while data_dir is writable and
        # already used for uploaded/rendered PDF pages.
        return self.data_dir / "custom_system_prompt.txt"

    @property
    def custom_user_prompt_path(self) -> Path:
        return self.data_dir / "custom_user_prompt.txt"

    def default_system_prompt_text(self) -> str:
        if self.custom_system_prompt_path.exists():
            return self.custom_system_prompt_path.read_text(encoding="utf-8")
        return self.read_prompt_file(self.default_system_prompt_file)

    def default_user_prompt_text(self) -> str:
        if self.custom_user_prompt_path.exists():
            return self.custom_user_prompt_path.read_text(encoding="utf-8")
        return self.read_prompt_file(self.default_user_prompt_file)

    def ai_crop_cell_prompt_text(self) -> str:
        return self.read_prompt_file(self.ai_crop_cell_prompt_file)

    def save_custom_system_prompt(self, text: str) -> None:
        self.custom_system_prompt_path.write_text(text, encoding="utf-8")

    def save_custom_user_prompt(self, text: str) -> None:
        self.custom_user_prompt_path.write_text(text, encoding="utf-8")

    def read_prompt_file(self, filename: str) -> str:
        path = self.prompts_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        return path.read_text(encoding="utf-8")

    @property
    def custom_models_path(self) -> Path:
        # Same reasoning as custom_{system,user}_prompt_path: config.yaml's
        # `models:` list is meant to be edited by redeploying, so models
        # added from the UI (e.g. typing "moonshotai/kimi-k3" straight in)
        # live in data_dir instead, which is writable and survives restarts.
        return self.data_dir / "custom_models.json"

    def custom_models(self) -> list[ModelOption]:
        """Read fresh from disk every call (not cached at startup like
        `self.models`), since these can be added/removed at any time while
        the backend keeps running -- unlike config.yaml, which only changes
        on redeploy."""
        if not self.custom_models_path.exists():
            return []
        try:
            raw = json.loads(self.custom_models_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return [ModelOption(**m) for m in raw]

    def all_models(self) -> list[ModelOption]:
        return self.models + self.custom_models()

    def add_custom_model(self, model_id: str) -> ModelOption:
        """Add a model by id alone -- always routed through OpenRouter, since
        that's what a bare "vendor/model-slug" string means in this app (see
        config.yaml's `models:` comment). Idempotent: adding an id that's
        already present (custom or from config.yaml) is a no-op, not an
        error, so the UI can just "ensure this model exists" without first
        checking for duplicates itself."""
        option = ModelOption(id=model_id, label=model_id, provider="openrouter", custom=True)
        existing = self.custom_models()
        already_known = any(m.id == model_id for m in self.models) or any(
            m.id == model_id for m in existing
        )
        if not already_known:
            existing.append(option)
            self._write_custom_models(existing)
        return option

    def remove_custom_model(self, model_id: str) -> None:
        """No-op if `model_id` isn't a custom model (e.g. it's one from
        config.yaml, or already removed) -- there's nothing to clean up."""
        remaining = [m for m in self.custom_models() if m.id != model_id]
        self._write_custom_models(remaining)

    def _write_custom_models(self, models: list[ModelOption]) -> None:
        self.custom_models_path.write_text(
            json.dumps([m.model_dump() for m in models], indent=2),
            encoding="utf-8",
        )

    def model_ids(self) -> list[str]:
        return [m.id for m in self.all_models()]

    def find_model(self, model_id: str) -> ModelOption | None:
        return next((m for m in self.all_models() if m.id == model_id), None)


@lru_cache
def get_settings() -> Settings:
    return Settings()
