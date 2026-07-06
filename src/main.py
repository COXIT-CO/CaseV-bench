import json
from pathlib import Path

from adapters.openrouter import send_image_prompt
from services.pdf_processing import PDFProcessingService
from utils import downsample, draw_overlay, next_run_key, parse_count_result, parse_detections

PROJECT = "prj0002"
PDF_PATH = Path(f"data/input/{PROJECT}.pdf")
MAX_LONG_EDGE = 1568

MODELS = [
    "anthropic/claude-sonnet-4.5",
    "openai/gpt-5-mini",
    "google/gemini-2.5-flash",
]

COUNTING_PROMPT_PATH = Path("src/prompts/object_counting/v0001.md")
LOCATION_PROMPT_PATH = Path("src/prompts/object_location/v0001.md")
OVERLAY_DIR = Path(f"data/output/{PROJECT}/object_location")
COUNTING_LOGS_PATH = Path("src/prompts/object_counting/logs.json")
LOCATION_LOGS_PATH = Path("src/prompts/object_location/logs.json")


def get_downsampled_page_1() -> Path:
    service = PDFProcessingService()
    image_paths = service.process(PDF_PATH)

    page_1 = image_paths[0]
    downsampled_page_1 = page_1.with_stem(f"{page_1.stem}_downsampled")
    downsample(page_1, downsampled_page_1, MAX_LONG_EDGE)
    return downsampled_page_1


def run_object_counting(
    image_path: Path | None = None, prompt_path: Path = COUNTING_PROMPT_PATH
) -> None:
    prompt = prompt_path.read_text()
    prompt_version = prompt_path.name

    logs = json.loads(COUNTING_LOGS_PATH.read_text()) if COUNTING_LOGS_PATH.exists() else {}
    model_results = []

    for model in MODELS:
        print(f"\n===== {model} =====")
        result = send_image_prompt(image_path, model, prompt, prefill_json=True)
        content = result["choices"][0]["message"]["content"]
        print(content)

        try:
            count_result = parse_count_result(content)
        except (json.JSONDecodeError, ValueError) as error:
            print(f"failed to parse response as JSON: {error}")
            model_results.append(
                {"model": model, "parse_error": str(error), "raw_content": content}
            )
            continue

        model_results.append({"model": model, **count_result.model_dump()})

    run_key = next_run_key(logs)
    logs[run_key] = {
        "prompt_version": prompt_version,
        "project": PROJECT,
        "model_results": model_results,
    }
    COUNTING_LOGS_PATH.write_text(json.dumps(logs, indent=4))


def run_location_detection(
    image_path: Path, prompt_path: Path = LOCATION_PROMPT_PATH
) -> None:
    prompt = prompt_path.read_text()
    prompt_version = prompt_path.name

    logs = json.loads(LOCATION_LOGS_PATH.read_text()) if LOCATION_LOGS_PATH.exists() else {}
    model_results = []

    for model in MODELS:
        print(f"\n===== {model} =====")
        result = send_image_prompt(image_path, model, prompt, prefill_json=True)
        content = result["choices"][0]["message"]["content"]
        print(content)

        try:
            detections = parse_detections(content)
        except (json.JSONDecodeError, ValueError) as error:
            print(f"failed to parse response as JSON: {error}")
            model_results.append(
                {"model": model, "parse_error": str(error), "raw_content": content}
            )
            continue

        model_results.append(
            {
                "model": model,
                "detection_count": len(detections),
                "detections": [detection.model_dump() for detection in detections],
            }
        )

        model_slug = model.replace("/", "_")
        overlay_path = OVERLAY_DIR / f"{prompt_version.removesuffix('.md')}_{model_slug}.png"
        draw_overlay(image_path, detections, overlay_path)
        print(f"overlay saved to {overlay_path}")

    run_key = next_run_key(logs)
    logs[run_key] = {
        "prompt_version": prompt_version,
        "project": PROJECT,
        "model_results": model_results,
    }
    LOCATION_LOGS_PATH.write_text(json.dumps(logs, indent=4))


if __name__ == "__main__":
    # image_path = get_downsampled_page_1()
    image_path = Path("data/output/prj0002/page_0001_downsampled.png")
    run_object_counting(image_path)
    run_location_detection(image_path)
