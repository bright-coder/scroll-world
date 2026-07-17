#!/usr/bin/env python3
"""Command-line contracts for Kie.ai video generation."""

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


DEFAULT_MODEL = "bytedance/seedance-2-fast"
VALID_ASPECT_RATIOS = {"16:9", "9:16"}
VALID_RESOLUTIONS = {"720p"}


class ValidationError(ValueError):
    """Raised when local generation inputs are invalid."""


@dataclass(frozen=True)
class GenerationConfig:
    prompt_file: Path
    start_image: Path
    end_image: Path | None
    output: Path
    model: str = DEFAULT_MODEL
    aspect_ratio: str = "16:9"
    resolution: str = "720p"
    duration: int = 15
    timeout_seconds: int = 900


def require_api_key() -> str:
    """Return the configured API key without ever accepting it from the CLI."""
    api_key = os.environ.get("KIE_API_KEY", "").strip()
    if not api_key:
        raise ValidationError("KIE_API_KEY must be set in the environment")
    return api_key


def _require_regular_file(path: Path, name: str) -> None:
    if not path.exists() or not path.is_file():
        raise ValidationError(f"{name} must be an existing regular file: {path}")


def validate_generation(config: GenerationConfig) -> None:
    """Validate generation inputs before any upload or paid task submission."""
    _require_regular_file(config.prompt_file, "prompt file")
    _require_regular_file(config.start_image, "start image")
    if config.end_image is not None:
        _require_regular_file(config.end_image, "end image")

    try:
        prompt = config.prompt_file.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as error:
        raise ValidationError("prompt file must be valid UTF-8 text") from error
    if not prompt:
        raise ValidationError("prompt file must not be empty")
    if config.aspect_ratio not in VALID_ASPECT_RATIOS:
        raise ValidationError("aspect ratio must be 16:9 or 9:16")
    if config.resolution not in VALID_RESOLUTIONS:
        raise ValidationError("resolution must be 720p")
    if config.duration <= 0:
        raise ValidationError("duration must be positive")
    if config.timeout_seconds <= 0:
        raise ValidationError("timeout seconds must be positive")
    if config.output.exists() and config.output.is_dir():
        raise ValidationError("output must not be an existing directory")


def build_task_payload(
    config: GenerationConfig, first_url: str, last_url: str | None = None
) -> dict[str, object]:
    """Build the Kie.ai create-task body for a dive or frame-locked connector."""
    inputs: dict[str, object] = {
        "prompt": config.prompt_file.read_text(encoding="utf-8").strip(),
        "first_frame_url": first_url,
        "return_last_frame": True,
        "generate_audio": False,
        "resolution": config.resolution,
        "aspect_ratio": config.aspect_ratio,
        "duration": config.duration,
        "web_search": False,
    }
    if last_url is not None:
        inputs["last_frame_url"] = last_url
    return {"model": config.model, "input": inputs}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse Kie generation and resume commands into a validated CLI namespace."""
    parser = argparse.ArgumentParser(description="Generate or resume Kie.ai videos.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    generate = subcommands.add_parser("generate-video", help="create a video task")
    generate.add_argument("--prompt-file", type=Path, required=True)
    generate.add_argument("--start-image", type=Path, required=True)
    generate.add_argument("--end-image", type=Path)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--model", default=os.environ.get("KIE_MODEL", DEFAULT_MODEL))
    generate.add_argument("--aspect-ratio", default="16:9")
    generate.add_argument("--resolution", default="720p")
    generate.add_argument("--duration", type=int, default=15)
    generate.add_argument("--timeout-seconds", type=int, default=900)

    wait = subcommands.add_parser("wait", help="resume an existing Kie.ai task")
    wait.add_argument("--manifest", type=Path, required=True)
    wait.add_argument("--output", type=Path, required=True)
    wait.add_argument("--timeout-seconds", type=int, default=900)

    args = parser.parse_args(argv)
    if args.command == "generate-video":
        args.config = GenerationConfig(
            prompt_file=args.prompt_file,
            start_image=args.start_image,
            end_image=args.end_image,
            output=args.output,
            model=args.model,
            aspect_ratio=args.aspect_ratio,
            resolution=args.resolution,
            duration=args.duration,
            timeout_seconds=args.timeout_seconds,
        )
        validate_generation(args.config)
    elif args.timeout_seconds <= 0:
        raise ValidationError("timeout seconds must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Validate CLI input; request execution is added in the next client task."""
    parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
