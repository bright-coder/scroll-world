#!/usr/bin/env python3
"""Command-line contracts for Kie.ai video generation."""

import argparse
import hashlib
import io
import ipaddress
import json
import mimetypes
import os
import random
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid
from contextlib import redirect_stderr
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlencode, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_MODEL = "bytedance/seedance-2-fast"
VALID_ASPECT_RATIOS = {"16:9", "9:16"}
VALID_RESOLUTIONS = {"720p"}
UPLOAD_URL = "https://kieai.redpandaai.co/api/file-stream-upload"
CREATE_TASK_URL = "https://api.kie.ai/api/v1/jobs/createTask"
TASK_INFO_URL = "https://api.kie.ai/api/v1/jobs/recordInfo"
MANIFEST_SCHEMA_VERSION = 1
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
ACTIVE_STATES = {"waiting", "queuing", "generating"}
VALIDATION_ERROR_EXIT = 2
CLIENT_ERROR_EXIT = 3
INTERRUPTED_EXIT = 130


class ValidationError(ValueError):
    """Raised when local generation inputs are invalid."""


class HttpError(RuntimeError):
    """Raised when a Kie.ai HTTP request cannot be completed."""


class TransientNetworkError(HttpError):
    """Raised when a safe Kie.ai request may succeed if retried."""


class UncertainSubmissionError(HttpError):
    """Raised when createTask may have accepted a paid task without returning its ID."""


class SchemaError(RuntimeError):
    """Raised when Kie.ai returns a response outside its documented schema."""


class TaskFailedError(RuntimeError):
    """Raised when Kie.ai marks a video-generation task as failed."""


class TaskTimeoutError(TimeoutError):
    """Raised when waiting for a Kie.ai task exceeds its configured deadline."""


class MediaValidationError(RuntimeError):
    """Raised when a downloaded result is not a valid video file."""


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
        except (URLError, TimeoutError) as error:
            raise TransientNetworkError(
                "network error while contacting Kie.ai"
            ) from error


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
    deadline: float | None = None,
    monotonic=time.monotonic,
    deadline_error=None,
) -> HttpResponse:
    """Retry only transient HTTP responses with bounded exponential backoff."""
    if (
        not isinstance(max_attempts, int)
        or isinstance(max_attempts, bool)
        or max_attempts <= 0
    ):
        raise ValueError("max_attempts must be a positive integer")
    def raise_for_deadline() -> None:
        if deadline_error is None:
            raise TaskTimeoutError("request deadline exceeded")
        raise deadline_error()

    def remaining_budget() -> float | None:
        if deadline is None:
            return None
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise_for_deadline()
        return remaining

    for attempt in range(max_attempts):
        remaining_budget()
        try:
            response = operation()
        except TransientNetworkError:
            remaining_budget()
            if attempt == max_attempts - 1:
                raise
            delay = min(30.0, 3.0 * (2**attempt)) + randomizer()
            remaining = remaining_budget()
            if remaining is not None:
                delay = min(delay, remaining)
            sleeper(delay)
            continue
        remaining_budget()
        if response.status not in RETRYABLE_STATUS or attempt == max_attempts - 1:
            return response
        delay = min(30.0, 3.0 * (2**attempt)) + randomizer()
        remaining = remaining_budget()
        if remaining is not None:
            delay = min(delay, remaining)
        sleeper(delay)
    raise AssertionError("unreachable")


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


def _existing_generation_message(
    path: Path, manifest: Mapping[str, object] | None
) -> str:
    if manifest is not None and manifest.get("state") == "submission_uncertain":
        return (
            "task submission outcome is uncertain; retain this manifest and "
            "investigate Kie task history or contact Kie support before authorizing "
            f"any replacement: {path}"
        )
    return (
        "existing generation reservation found; use wait or choose a new output: "
        f"{path}"
    )


def ensure_new_generation(path: Path) -> None:
    """Reject generation when a saved task should be resumed with ``wait``."""
    manifest = load_manifest(path)
    if manifest is not None:
        raise ValidationError(_existing_generation_message(path, manifest))


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
    except TransientNetworkError as error:
        raise UncertainSubmissionError(
            "task creation outcome is uncertain; it was not retried to avoid duplicate spend"
        ) from error
    if response.status != 200:
        messages = {
            400: "task creation request was rejected",
            401: "authentication failed while creating task",
            402: "payment or credits are required to create task",
        }
        message = messages.get(
            response.status,
            f"task creation failed with HTTP status {response.status}",
        )
        error_type = UncertainSubmissionError if response.status >= 500 else HttpError
        raise error_type(f"{message}; it was not retried to avoid duplicate spend")
    try:
        response_payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UncertainSubmissionError(
            "task creation returned an invalid JSON response; "
            "it was not retried to avoid duplicate spend"
        ) from error
    if not isinstance(response_payload, dict):
        raise UncertainSubmissionError(
            "task creation returned an invalid response schema; "
            "it was not retried to avoid duplicate spend"
        )
    data = response_payload.get("data")
    task_id = data.get("taskId") if isinstance(data, dict) else None
    if response_payload.get("code") != 200 or not isinstance(task_id, str) or not task_id:
        raise UncertainSubmissionError(
            "task creation returned an invalid response schema; "
            "it was not retried to avoid duplicate spend"
        )
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


def _reservation_manifest(
    config: GenerationConfig, reservation_id: str
) -> dict[str, object]:
    timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "reservationId": reservation_id,
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
        "uploadedUrls": {"firstFrame": None, "lastFrame": None},
        "taskId": None,
        "state": "reserved",
        "outputPath": str(config.output),
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }


def reserve_generation(config: GenerationConfig) -> str:
    """Exclusively reserve and preflight the sidecar before any remote operation."""
    manifest_path = manifest_path_for(config.output)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    reservation_id = uuid.uuid4().hex
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(manifest_path, flags, 0o600)
    except FileExistsError as error:
        try:
            manifest = load_manifest(manifest_path)
        except ValidationError:
            manifest = None
        raise ValidationError(
            _existing_generation_message(manifest_path, manifest)
        ) from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as manifest_file:
            json.dump(
                _reservation_manifest(config, reservation_id),
                manifest_file,
                indent=2,
                sort_keys=True,
            )
            manifest_file.write("\n")
            manifest_file.flush()
            os.fsync(manifest_file.fileno())
    except BaseException:
        manifest_path.unlink(missing_ok=True)
        raise
    return reservation_id


def release_generation_reservation(path: Path, reservation_id: str) -> None:
    """Release only this process's definite pre-submission reservation."""
    manifest = load_manifest(path)
    if (
        manifest is not None
        and manifest.get("reservationId") == reservation_id
        and manifest.get("state") == "reserved"
        and manifest.get("taskId") is None
    ):
        Path(path).unlink(missing_ok=True)


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
    reservation_id: str | None = None,
) -> str:
    """Submit uploaded inputs and immediately persist a resumable task manifest."""
    manifest_path = manifest_path_for(config.output)
    if reservation_id is None:
        reservation_id = reserve_generation(config)
    manifest = load_manifest(manifest_path)
    if (
        manifest is None
        or manifest.get("reservationId") != reservation_id
        or manifest.get("state") != "reserved"
    ):
        raise ValidationError(f"generation reservation is not owned: {manifest_path}")

    submission_started = False
    try:
        payload = build_task_payload(config, first_url, last_url)
        manifest["uploadedUrls"] = {
            "firstFrame": first_url,
            "lastFrame": last_url,
        }
        manifest["updatedAt"] = datetime.now(timezone.utc).isoformat()
        write_manifest_atomic(manifest_path, manifest)
        submission_started = True
        task_id = create_task(payload, api_key, transport)
    except UncertainSubmissionError as error:
        manifest["state"] = "submission_uncertain"
        manifest["lastError"] = str(error)
        manifest["updatedAt"] = datetime.now(timezone.utc).isoformat()
        write_manifest_atomic(manifest_path, manifest)
        raise
    except KeyboardInterrupt:
        if submission_started:
            manifest["state"] = "submission_uncertain"
            manifest["lastError"] = (
                "task creation was interrupted and was not retried to avoid duplicate spend"
            )
            manifest["updatedAt"] = datetime.now(timezone.utc).isoformat()
            write_manifest_atomic(manifest_path, manifest)
        else:
            release_generation_reservation(manifest_path, reservation_id)
        raise
    except BaseException:
        release_generation_reservation(manifest_path, reservation_id)
        raise

    timestamp = datetime.now(timezone.utc).isoformat()
    manifest.update(
        {
            "taskId": task_id,
            "state": "waiting",
            "updatedAt": timestamp,
        }
    )
    write_manifest_atomic(manifest_path, manifest)
    return task_id


def _is_valid_hostname(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass
    if len(hostname) > 253 or all(
        character.isdigit() or character == "." for character in hostname
    ):
        return False
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    if ascii_hostname.endswith("."):
        ascii_hostname = ascii_hostname[:-1]
    labels = ascii_hostname.split(".")
    return bool(labels) and all(
        label
        and len(label) <= 63
        and not label.startswith("-")
        and not label.endswith("-")
        and all(character.isalnum() or character == "-" for character in label)
        for label in labels
    )


def _is_valid_https_result_url(result_url: object) -> bool:
    if not isinstance(result_url, str) or not result_url:
        return False
    if any(
        character.isspace()
        or unicodedata.category(character).startswith("C")
        for character in result_url
    ):
        return False
    try:
        parsed = urlparse(result_url)
        hostname = parsed.hostname
        port = parsed.port
        username = parsed.username
        password = parsed.password
    except (UnicodeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and hostname is not None
        and _is_valid_hostname(hostname)
        and username is None
        and password is None
        and (port is None or 1 <= port <= 65535)
    )


def parse_task_record(data: Mapping[str, object]) -> tuple[str, str | None]:
    """Parse one documented Kie.ai task record into its state and result URL."""
    state = data.get("state")
    if state in ACTIVE_STATES:
        return state, None
    if state == "fail":
        raise TaskFailedError(f"{data.get('failCode', '')}: {data.get('failMsg', '')}")
    if state != "success":
        raise SchemaError(f"unexpected task state: {state!r}")
    try:
        result_json = data["resultJson"]
        if not isinstance(result_json, str):
            raise TypeError("resultJson must be a string")
        result = json.loads(result_json)
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise SchemaError("successful task did not contain valid result JSON") from error
    result_urls = result.get("resultUrls") if isinstance(result, dict) else None
    if not isinstance(result_urls, list) or not result_urls:
        raise SchemaError("resultUrls must be a non-empty list of HTTPS URLs")
    for result_url in result_urls:
        if not _is_valid_https_result_url(result_url):
            raise SchemaError("resultUrls must be a non-empty list of HTTPS URLs")
    return state, result_urls[0]


def _resume_command(manifest_path: Path | None, output_path: Path | None) -> str:
    if manifest_path is None or output_path is None:
        return "wait with the same task ID"
    return f"wait --manifest {manifest_path} --output {output_path}"


def _persist_task_observation(
    manifest_path: Path | None,
    task_id: str,
    record: Mapping[str, object],
) -> None:
    if manifest_path is None:
        return
    manifest = load_manifest(manifest_path) or {"taskId": task_id}
    manifest["taskId"] = task_id
    manifest["state"] = record.get("state")
    if "progress" in record:
        manifest["progress"] = record["progress"]
    manifest["updatedAt"] = datetime.now(timezone.utc).isoformat()
    write_manifest_atomic(manifest_path, manifest)


def wait_for_task(
    task_id: str,
    api_key: str,
    transport: UrllibTransport,
    sleeper=time.sleep,
    randomizer=random.random,
    timeout_seconds: int = 900,
    manifest_path: Path | None = None,
    output_path: Path | None = None,
    monotonic=time.monotonic,
) -> str:
    """Poll a saved Kie.ai task until it succeeds, fails, or times out."""
    if not task_id:
        raise ValidationError("task ID must not be empty")
    if not api_key:
        raise ValidationError("KIE_API_KEY must be set in the environment")
    if timeout_seconds <= 0:
        raise ValidationError("timeout seconds must be positive")

    deadline = monotonic() + timeout_seconds
    attempt = 0
    resume_command = _resume_command(manifest_path, output_path)

    def timeout_error() -> TaskTimeoutError:
        return TaskTimeoutError(
            f"timed out waiting for task {task_id}; resume with {resume_command}"
        )

    def remaining_budget() -> float:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise timeout_error()
        return remaining

    def poll_once() -> HttpResponse:
        request_timeout = min(60.0, remaining_budget())
        return transport.request(
            "GET",
            f"{TASK_INFO_URL}?{urlencode({'taskId': task_id})}",
            {"Authorization": f"Bearer {api_key}"},
            timeout=request_timeout,
        )

    try:
        while True:
            response = request_with_retry(
                poll_once,
                sleeper=sleeper,
                randomizer=randomizer,
                deadline=deadline,
                monotonic=monotonic,
                deadline_error=timeout_error,
            )
            if response.status != 200:
                messages = {
                    400: "task poll request was rejected",
                    401: "authentication failed while polling task",
                    402: "payment or credits are required to poll task",
                }
                raise HttpError(
                    messages.get(
                        response.status,
                        f"task polling failed with HTTP status {response.status}",
                    )
                )
            try:
                payload = json.loads(response.body.decode("utf-8"))
                record = payload["data"] if isinstance(payload, dict) else None
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as error:
                raise SchemaError("task polling returned an invalid JSON response") from error
            if (
                not isinstance(payload, dict)
                or payload.get("code") != 200
                or not isinstance(record, dict)
            ):
                raise SchemaError("task polling returned an invalid response schema")

            _persist_task_observation(manifest_path, task_id, record)
            state, result_url = parse_task_record(record)
            if result_url is not None:
                return result_url
            delay = min(30.0, 3.0 * (2 ** min(attempt, 4))) + randomizer()
            sleeper(min(delay, remaining_budget()))
            attempt += 1
    except KeyboardInterrupt:
        raise KeyboardInterrupt(f"interrupted while waiting; resume with {resume_command}")


def probe_video(path: Path, runner=subprocess.run) -> dict[str, object]:
    """Return metadata for the first video stream reported by FFprobe."""
    try:
        completed = runner(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height,duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise MediaValidationError("ffprobe could not validate the download") from error
    streams = payload.get("streams", []) if isinstance(payload, dict) else []
    if (
        not isinstance(streams, list)
        or not streams
        or not isinstance(streams[0], dict)
        or not streams[0].get("codec_name")
    ):
        raise MediaValidationError("download did not contain a video stream")
    return streams[0]


def require_successful_http(response: HttpResponse) -> None:
    """Raise a focused download error for a non-successful HTTP response."""
    if 200 <= response.status < 300:
        return
    messages = {
        400: "video download request was rejected",
        401: "authentication failed while downloading video",
        402: "payment or credits are required to download video",
        404: "generated video was not found",
    }
    raise HttpError(
        messages.get(
            response.status,
            f"video download failed with HTTP status {response.status}",
        )
    )


def download_result(
    url: str,
    output: Path,
    transport: UrllibTransport,
    probe=probe_video,
    sleeper=time.sleep,
    randomizer=random.random,
) -> dict[str, object]:
    """Download, validate, and atomically install a generated video result."""
    output = Path(output)
    part = Path(str(output) + ".part")
    response = request_with_retry(
        lambda: transport.request("GET", url, {}, None),
        sleeper=sleeper,
        randomizer=randomizer,
    )
    require_successful_http(response)
    output.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(response.body)
    stream = probe(part)
    os.replace(part, output)
    return stream


def _finalize_manifest(
    manifest_path: Path,
    output: Path,
    task_id: str,
    result_url: str,
) -> dict[str, object]:
    manifest = load_manifest(manifest_path) or {}
    timestamp = datetime.now(timezone.utc).isoformat()
    manifest.update(
        {
            "schemaVersion": MANIFEST_SCHEMA_VERSION,
            "taskId": task_id,
            "state": "success",
            "resultUrl": result_url,
            "outputPath": str(output),
            "updatedAt": timestamp,
            "completedAt": timestamp,
        }
    )
    write_manifest_atomic(manifest_path, manifest)
    return {
        "status": "success",
        "taskId": task_id,
        "resultUrl": result_url,
        "output": str(output),
        "manifest": str(manifest_path),
    }


def _persist_result_observation(
    manifest_path: Path,
    output: Path,
    result_url: str,
) -> None:
    """Keep the provider result resumable even when local validation fails."""
    manifest = load_manifest(manifest_path) or {}
    manifest["state"] = "success"
    manifest["resultUrl"] = result_url
    manifest["outputPath"] = str(output)
    manifest["updatedAt"] = datetime.now(timezone.utc).isoformat()
    write_manifest_atomic(manifest_path, manifest)


def run_generate(
    config: GenerationConfig,
    api_key: str,
    transport: UrllibTransport,
    probe=probe_video,
    sleeper=time.sleep,
    randomizer=random.random,
    monotonic=time.monotonic,
) -> dict[str, object]:
    """Run a new generation from local validation through atomic download."""
    validate_generation(config)
    manifest_path = manifest_path_for(config.output)
    reservation_id = reserve_generation(config)
    try:
        first_url = upload_frame(
            config.start_image,
            api_key,
            transport,
            sleeper=sleeper,
            randomizer=randomizer,
        )
        last_url = None
        if config.end_image is not None:
            last_url = upload_frame(
                config.end_image,
                api_key,
                transport,
                sleeper=sleeper,
                randomizer=randomizer,
            )
    except BaseException:
        release_generation_reservation(manifest_path, reservation_id)
        raise
    task_id = submit_generation(
        config,
        first_url,
        last_url,
        api_key,
        transport,
        reservation_id=reservation_id,
    )
    result_url = wait_for_task(
        task_id,
        api_key,
        transport,
        sleeper=sleeper,
        randomizer=randomizer,
        timeout_seconds=config.timeout_seconds,
        manifest_path=manifest_path,
        output_path=config.output,
        monotonic=monotonic,
    )
    _persist_result_observation(manifest_path, config.output, result_url)
    download_result(
        result_url,
        config.output,
        transport,
        probe=probe,
        sleeper=sleeper,
        randomizer=randomizer,
    )
    return _finalize_manifest(manifest_path, config.output, task_id, result_url)


def run_wait(
    manifest_path: Path,
    output: Path,
    api_key: str,
    transport: UrllibTransport,
    probe=probe_video,
    sleeper=time.sleep,
    randomizer=random.random,
    timeout_seconds: int | None = None,
    monotonic=time.monotonic,
) -> dict[str, object]:
    """Resume a persisted task without issuing another create request."""
    manifest_path = Path(manifest_path)
    output = Path(output)
    manifest = load_manifest(manifest_path)
    if manifest is None:
        raise ValidationError(f"manifest does not exist: {manifest_path}")
    task_id = manifest.get("taskId")
    if not isinstance(task_id, str) or not task_id:
        raise ValidationError(f"manifest does not contain a task ID: {manifest_path}")
    if output.exists() and output.is_dir():
        raise ValidationError("output must not be an existing directory")
    if timeout_seconds is None:
        parameters = manifest.get("parameters")
        saved_timeout = (
            parameters.get("timeoutSeconds")
            if isinstance(parameters, dict)
            else None
        )
        timeout_seconds = saved_timeout if isinstance(saved_timeout, int) else 900
    if timeout_seconds <= 0:
        raise ValidationError("timeout seconds must be positive")

    result_url = wait_for_task(
        task_id,
        api_key,
        transport,
        sleeper=sleeper,
        randomizer=randomizer,
        timeout_seconds=timeout_seconds,
        manifest_path=manifest_path,
        output_path=output,
        monotonic=monotonic,
    )
    _persist_result_observation(manifest_path, output, result_url)
    download_result(
        result_url,
        output,
        transport,
        probe=probe,
        sleeper=sleeper,
        randomizer=randomizer,
    )
    return _finalize_manifest(manifest_path, output, task_id, result_url)


def redact(text: str, api_key: str) -> str:
    """Remove an API key from text destined for logs or machine output."""
    return text.replace(api_key, "[REDACTED]") if api_key else text


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
    """Run the requested workflow with JSON output and stable client exit codes."""
    api_key = os.environ.get("KIE_API_KEY", "").strip()
    parser_stderr = io.StringIO()
    try:
        with redirect_stderr(parser_stderr):
            args = parse_args(argv)
        api_key = require_api_key()
        transport = UrllibTransport()
        if args.command == "generate-video":
            print(f"Generating video for {args.config.output}...", file=sys.stderr)
            result = run_generate(args.config, api_key, transport)
        else:
            print(f"Waiting for task in {args.manifest}...", file=sys.stderr)
            result = run_wait(
                args.manifest,
                args.output,
                api_key,
                transport,
                timeout_seconds=args.timeout_seconds,
            )
        print("Video ready.", file=sys.stderr)
        print(json.dumps(result, sort_keys=True))
        return 0
    except SystemExit as error:
        details = redact(parser_stderr.getvalue(), api_key)
        if details:
            print(details, end="", file=sys.stderr)
        if error.code == 0:
            return 0
        message = redact("invalid command-line arguments", api_key)
        print(json.dumps({"status": "error", "error": message}, sort_keys=True))
        return VALIDATION_ERROR_EXIT
    except ValidationError as error:
        message = redact(str(error), api_key)
        print(f"Error: {message}", file=sys.stderr)
        print(json.dumps({"status": "error", "error": message}, sort_keys=True))
        return VALIDATION_ERROR_EXIT
    except (
        HttpError,
        SchemaError,
        TaskFailedError,
        TaskTimeoutError,
        MediaValidationError,
        OSError,
    ) as error:
        message = redact(str(error), api_key)
        print(f"Error: {message}", file=sys.stderr)
        print(json.dumps({"status": "error", "error": message}, sort_keys=True))
        return CLIENT_ERROR_EXIT
    except KeyboardInterrupt as error:
        message = redact(str(error) or "interrupted", api_key)
        print(f"Error: {message}", file=sys.stderr)
        print(json.dumps({"status": "error", "error": message}, sort_keys=True))
        return INTERRUPTED_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
