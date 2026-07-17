#!/usr/bin/env python3
"""Command-line contracts for Kie.ai video generation."""

import argparse
import hashlib
import json
import mimetypes
import os
import random
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_MODEL = "bytedance/seedance-2-fast"
VALID_ASPECT_RATIOS = {"16:9", "9:16"}
VALID_RESOLUTIONS = {"720p"}
UPLOAD_URL = "https://kieai.redpandaai.co/api/file-stream-upload"
CREATE_TASK_URL = "https://api.kie.ai/api/v1/jobs/createTask"
MANIFEST_SCHEMA_VERSION = 1
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ValidationError(ValueError):
    """Raised when local generation inputs are invalid."""


class HttpError(RuntimeError):
    """Raised when a Kie.ai HTTP request cannot be completed."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class UrllibTransport:
    """Small urllib-based HTTP transport with an injectable test boundary."""

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None = None,
        timeout: int = 60,
    ) -> HttpResponse:
        request = Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                return HttpResponse(
                    status=response.status,
                    headers=dict(response.headers.items()),
                    body=response.read(),
                )
        except HTTPError as error:
            return HttpResponse(
                status=error.code,
                headers=dict(error.headers.items()) if error.headers else {},
                body=error.read(),
            )
        except URLError as error:
            raise HttpError("network error while contacting Kie.ai") from error


def encode_multipart_file(
    path: Path, upload_path: str = "images/scroll-world"
) -> tuple[str, bytes]:
    """Encode a frame upload without including credentials in the body."""
    contents = path.read_bytes()
    digest = hashlib.sha256(contents).hexdigest()[:12]
    remote_name = f"{path.stem}-{digest}{path.suffix.lower()}"
    boundary = f"----scrollworld-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in (("uploadPath", upload_path), ("fileName", remote_name)):
        chunks.extend(
            (
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            )
        )
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    chunks.extend(
        (
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{remote_name}"\r\n'.encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            contents,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        )
    )
    return boundary, b"".join(chunks)


def request_with_retry(
    operation,
    sleeper=time.sleep,
    randomizer=random.random,
    max_attempts: int = 4,
) -> HttpResponse:
    """Retry only transient HTTP responses with bounded exponential backoff."""
    for attempt in range(max_attempts):
        response = operation()
        if response.status not in RETRYABLE_STATUS or attempt == max_attempts - 1:
            return response
        sleeper(min(30.0, 3.0 * (2**attempt)) + randomizer())
    raise AssertionError("max_attempts must be positive")


def _upload_error_message(status: int) -> str:
    messages = {
        400: "upload request was rejected",
        401: "authentication failed while uploading frame",
        402: "payment or credits are required to upload frame",
    }
    return messages.get(status, f"frame upload failed with HTTP status {status}")


def upload_frame(
    path: Path,
    api_key: str,
    transport: UrllibTransport,
    sleeper=time.sleep,
    randomizer=random.random,
) -> str:
    """Upload one local frame and return Kie.ai's temporary download URL."""
    _require_regular_file(path, "frame")
    if not api_key:
        raise ValidationError("KIE_API_KEY must be set in the environment")
    boundary, body = encode_multipart_file(path)
    response = request_with_retry(
        lambda: transport.request(
            "POST",
            UPLOAD_URL,
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            body,
        ),
        sleeper=sleeper,
        randomizer=randomizer,
    )
    if response.status != 200:
        raise HttpError(_upload_error_message(response.status))
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HttpError("frame upload returned an invalid JSON response") from error
    if not isinstance(payload, dict):
        raise HttpError("frame upload returned an invalid response schema")
    data = payload.get("data")
    download_url = data.get("downloadUrl") if isinstance(data, dict) else None
    if (
        payload.get("success") is not True
        or payload.get("code") != 200
        or not isinstance(download_url, str)
        or not download_url
    ):
        raise HttpError("frame upload returned an invalid response schema")
    return download_url


def manifest_path_for(output: Path) -> Path:
    """Return the sidecar path used to persist a resumable task identity."""
    return Path(str(output) + ".kie.json")


def write_manifest_atomic(path: Path, data: Mapping[str, object]) -> None:
    """Atomically replace a generation manifest with JSON data."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary_file:
        temporary = Path(temporary_file.name)
        json.dump(data, temporary_file, indent=2, sort_keys=True)
        temporary_file.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_manifest(path: Path) -> dict[str, object] | None:
    """Load a manifest, returning ``None`` when no previous task exists."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValidationError(f"manifest is not valid JSON: {path}") from error
    if not isinstance(data, dict):
        raise ValidationError(f"manifest must contain a JSON object: {path}")
    return data


def ensure_new_generation(path: Path) -> None:
    """Reject generation when a saved task should be resumed with ``wait``."""
    manifest = load_manifest(path)
    if manifest is not None and isinstance(manifest.get("taskId"), str) and manifest["taskId"]:
        raise ValidationError(f"existing task manifest found; use wait: {path}")


def create_task(
    payload: Mapping[str, object], api_key: str, transport: UrllibTransport
) -> str:
    """Submit a Kie.ai task and return its server-issued task identifier."""
    if not api_key:
        raise ValidationError("KIE_API_KEY must be set in the environment")
    try:
        response = transport.request(
            "POST",
            CREATE_TASK_URL,
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json.dumps(payload).encode("utf-8"),
        )
    except HttpError as error:
        raise HttpError(
            "task creation was not retried to avoid duplicate spend"
        ) from error
    if response.status != 200:
        raise HttpError(
            f"task creation failed with HTTP status {response.status}; "
            "it was not retried to avoid duplicate spend"
        )
    try:
        response_payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HttpError("task creation returned an invalid JSON response") from error
    if not isinstance(response_payload, dict):
        raise HttpError("task creation returned an invalid response schema")
    data = response_payload.get("data")
    task_id = data.get("taskId") if isinstance(data, dict) else None
    if response_payload.get("code") != 200 or not isinstance(task_id, str) or not task_id:
        raise HttpError("task creation returned an invalid response schema")
    return task_id


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


def submit_generation(
    config: GenerationConfig,
    first_url: str,
    last_url: str | None,
    api_key: str,
    transport: UrllibTransport,
) -> str:
    """Submit uploaded inputs and immediately persist a resumable task manifest."""
    manifest_path = manifest_path_for(config.output)
    ensure_new_generation(manifest_path)
    task_id = create_task(
        build_task_payload(config, first_url, last_url), api_key, transport
    )
    timestamp = datetime.now(timezone.utc).isoformat()
    write_manifest_atomic(
        manifest_path,
        {
            "schemaVersion": MANIFEST_SCHEMA_VERSION,
            "model": config.model,
            "parameters": {
                "aspectRatio": config.aspect_ratio,
                "duration": config.duration,
                "resolution": config.resolution,
                "timeoutSeconds": config.timeout_seconds,
            },
            "localInputs": {
                "promptFile": str(config.prompt_file),
                "startImage": str(config.start_image),
                "endImage": str(config.end_image) if config.end_image else None,
            },
            "uploadedUrls": {"firstFrame": first_url, "lastFrame": last_url},
            "taskId": task_id,
            "state": "waiting",
            "outputPath": str(config.output),
            "createdAt": timestamp,
            "updatedAt": timestamp,
        },
    )
    return task_id


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
