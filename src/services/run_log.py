import json
from pathlib import Path

from pydantic import BaseModel


class RunLogService:
    def __init__(self, task_dir: str, logs_root: Path):
        self.logs_dir = logs_root / task_dir

    def append_run(
        self, prompt_version: str, project: str, model_results: list[BaseModel]
    ) -> str:
        log_path = self._log_path(project, prompt_version)
        runs = self.load_runs(log_path)
        run_id = self._next_run_id(runs)
        runs[run_id] = {
            "prompt_version": prompt_version,
            "project": project,
            "model_results": [
                result.model_dump(mode="json") for result in model_results
            ],
        }
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(runs, indent=4))
        return run_id

    def load_runs(self, log_path: Path) -> dict:
        if not log_path or not log_path.exists():
            return {}
        return json.loads(log_path.read_text())

    def _next_run_id(self, runs: dict) -> str:
        existing = [int(key.split("_")[1]) for key in runs if key.startswith("run_")]
        return f"run_{max(existing, default=0) + 1}"

    def _log_path(self, project: str, prompt_version: str) -> Path:
        prompt_slug = Path(prompt_version).stem
        return self.logs_dir / f"{project}_{prompt_slug}.json"
