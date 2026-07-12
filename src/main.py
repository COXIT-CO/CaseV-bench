import json
from pathlib import Path

from adapters.openrouter import send_image_prompt
from cli import parse_args
from models.results import CountResult, LocationDetection, LocationResult
from models.run_log import ModelFailure, ModelSuccess
from services.pdf_processing import PDFProcessingService
from services.run_log import RunLogService
from utils import downsample, draw_overlay, parse_json


def run_object_counting(
    models: list[str], prompt_path: Path, image_path: Path, page: int = 0
) -> list[ModelSuccess[CountResult] | ModelFailure]:
    prompt = prompt_path.read_text()
    model_results = []

    for model in models:
        print(f"\n===== {model} =====")
        result = send_image_prompt(image_path, model, prompt, prefill_json=True)
        content = result["choices"][0]["message"]["content"]
        print(content)

        try:
            count_result = CountResult(**parse_json(content))
        except (json.JSONDecodeError, ValueError) as error:
            print(f"failed to parse response as JSON: {error}")
            model_results.append(
                ModelFailure(
                    model=model, parse_error=str(error), raw_content=content, page=page
                )
            )
            continue

        model_results.append(
            ModelSuccess[CountResult](model=model, result=count_result, page=page)
        )

    return model_results


def run_location_detection(
    models: list[str],
    prompt_path: Path,
    image_path: Path,
    overlay_dir: Path,
    page: int = 0,
) -> None:
    prompt = prompt_path.read_text()
    prompt_version = prompt_path.name
    model_results: list[ModelSuccess[LocationResult] | ModelFailure] = []

    for model in models:
        print(f"\n===== {model} =====")
        result = send_image_prompt(image_path, model, prompt, prefill_json=True)
        content = result["choices"][0]["message"]["content"]
        print(content)

        try:
            detections = [
                LocationDetection(**detection) for detection in parse_json(content)
            ]
        except (json.JSONDecodeError, ValueError) as error:
            print(f"failed to parse response as JSON: {error}")
            model_results.append(
                ModelFailure(
                    model=model, parse_error=str(error), raw_content=content, page=page
                )
            )
            continue

        model_results.append(
            ModelSuccess[LocationResult](
                model=model, result=LocationResult(detections=detections), page=page
            )
        )

        model_slug = model.replace("/", "_")
        overlay_path = (
            overlay_dir
            / f"{prompt_version.removesuffix('.md')}_{model_slug}_page_{page}.png"
        )
        draw_overlay(image_path, detections, overlay_path)
        print(f"overlay saved to {overlay_path}")

    return model_results


if __name__ == "__main__":
    args = parse_args()
    service = PDFProcessingService(output_dir=args.output_dir)
    image_paths = service.extract_images(args.pdf_path)

    if args.task == "object_counting":
        results = []
        for ind, image in enumerate(image_paths, start=1):
            downsampled_image = image.with_stem(f"{image.stem}_downsampled")
            downsample(image, downsampled_image)
            results.extend(
                run_object_counting(
                    models=args.models,
                    prompt_path=args.counting_prompt_path,
                    image_path=downsampled_image,
                    page=ind,
                )
            )

        run_log_service = RunLogService("object_counting", logs_root=args.logs_dir)
        run_log_service.append_run(
            args.counting_prompt_path.name, args.project, results
        )
    else:
        results = []
        for ind, image in enumerate(image_paths, start=1):
            downsampled_image = image.with_stem(f"{image.stem}_downsampled")
            downsample(
                image,
                downsampled_image,
            )
            results.extend(
                run_location_detection(
                    models=args.models,
                    prompt_path=args.location_prompt_path,
                    image_path=downsampled_image,
                    overlay_dir=args.overlay_dir,
                    page=ind,
                )
            )
        run_log_service = RunLogService("object_location", logs_root=args.logs_dir)
        run_log_service.append_run(
            args.location_prompt_path.name, args.project, results
        )
