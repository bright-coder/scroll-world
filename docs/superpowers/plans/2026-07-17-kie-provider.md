# Kie.ai Video Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an end-to-end Kie.ai video workflow that uploads local frames, generates `bytedance/seedance-2-fast` videos, resumes asynchronous tasks safely, and works with Codex-generated scene stills while preserving Higgsfield as a fallback.

**Architecture:** A single dependency-free Python CLI owns Kie.ai HTTP, manifest, polling, download, and media-validation behavior. The existing skill remains documentation-driven: a new Kie pipeline is the default, while the current Higgsfield pipeline remains available. Codex orchestrates built-in `image_gen` outside the Python process, saves selected stills into the project, normalizes their canvases, and passes those local files to the Kie CLI.

**Tech Stack:** Python 3 standard library (`argparse`, `urllib`, `json`, `unittest`), Kie.ai REST APIs, Bash 3.2-safe examples, FFmpeg/FFprobe, Codex built-in `image_gen`.

## Global Constraints

- Default video model: exactly `bytedance/seedance-2-fast`.
- No third-party Python dependencies.
- Read `KIE_API_KEY` only from the process environment; never accept it as a CLI argument.
- Never write credentials or authorization headers to logs, manifests, fixtures, or Git.
- A persisted `taskId` must be resumed, never silently resubmitted.
- Dive requests send `first_frame_url`; connector requests send `first_frame_url` and `last_frame_url` and no `reference_*` fields.
- Poll locally; do not add a webhook service.
- Keep `skills/scroll-world/references/pipeline.md` usable as the Higgsfield fallback.
- Use `apply_patch` for file edits and prefix shell commands with `rtk` in this workspace.
- Run all automated tests without network access before the single paid smoke test.

---

## File Map

- Create `skills/scroll-world/scripts/kie_client.py`: CLI, validation, HTTP transport, multipart upload, task lifecycle, atomic manifest, download, FFprobe validation.
- Create `skills/scroll-world/tests/test_kie_client.py`: standard-library unit and CLI tests with injected fake transport, sleeper, randomizer, and media probe.
- Create `skills/scroll-world/references/pipeline-kie.md`: default copy-paste Kie.ai workflow for dives, connectors, resume, extraction, encoding, and mobile.
- Create `.env.example`: safe environment-variable names with empty credential value.
- Modify `.gitignore`: ignore secrets, Kie manifests, partial downloads, and smoke-test artifacts.
- Modify `skills/scroll-world/SKILL.md`: route Codex still generation to built-in `image_gen`, Kie video generation to the new pipeline, and Higgsfield to fallback.
- Modify `README.md`: explain the fork's defaults, requirements, and provider matrix.

---

### Task 1: CLI contracts, validation, and task payloads

**Files:**
- Create: `skills/scroll-world/scripts/kie_client.py`
- Create: `skills/scroll-world/tests/test_kie_client.py`

**Interfaces:**
- Produces: `GenerationConfig`, `ValidationError`, `parse_args(argv)`, `validate_generation(config)`, and `build_task_payload(config, first_url, last_url=None)`.
- `GenerationConfig` fields: `prompt_file: Path`, `start_image: Path`, `end_image: Path | None`, `output: Path`, `model: str`, `aspect_ratio: str`, `resolution: str`, `duration: int`, `timeout_seconds: int`.

- [ ] **Step 1: Write failing validation and payload tests**

```python
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import kie_client

def make_config(end_image=None):
    directory = Path(tempfile.mkdtemp())
    prompt = directory / "prompt.txt"
    start = directory / "start.png"
    prompt.write_text("move forward", encoding="utf-8")
    start.write_bytes(b"png")
    return kie_client.GenerationConfig(
        prompt_file=prompt,
        start_image=start,
        end_image=end_image,
        output=directory / "clip.mp4",
    )

class PayloadTests(unittest.TestCase):
    def test_dive_payload_has_only_first_frame(self):
        config = make_config(end_image=None)
        payload = kie_client.build_task_payload(config, "https://files/start.png")
        self.assertEqual(payload["model"], "bytedance/seedance-2-fast")
        self.assertEqual(payload["input"]["first_frame_url"], "https://files/start.png")
        self.assertNotIn("last_frame_url", payload["input"])
        self.assertFalse(any(key.startswith("reference_") for key in payload["input"]))
        self.assertFalse(payload["input"]["generate_audio"])
        self.assertFalse(payload["input"]["web_search"])

    def test_connector_payload_has_first_and_last_frames(self):
        config = make_config(end_image=Path("end.png"))
        payload = kie_client.build_task_payload(
            config, "https://files/start.png", "https://files/end.png"
        )
        self.assertEqual(payload["input"]["last_frame_url"], "https://files/end.png")

    def test_missing_api_key_is_rejected_by_cli(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(kie_client.ValidationError, "KIE_API_KEY"):
                kie_client.require_api_key()

# Keep this entry point at the end of the test file as later test classes are added.
if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `rtk python3 skills/scroll-world/tests/test_kie_client.py PayloadTests -v`

Expected: import or attribute failures because `kie_client.py` and its contracts do not exist.

- [ ] **Step 3: Implement the data model, parser, validation, and payload builder**

```python
import argparse
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "bytedance/seedance-2-fast"

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

def build_task_payload(config, first_url, last_url=None):
    inputs = {
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
```

Implement `generate-video` and `wait` parsers with exact named arguments from the design. Validation must require existing regular input files, a non-empty prompt, `16:9` or `9:16`, `720p`, positive duration and timeout, and a destination that is not an existing directory.

- [ ] **Step 4: Run tests and CLI help**

Run: `rtk python3 skills/scroll-world/tests/test_kie_client.py PayloadTests -v`

Expected: all `PayloadTests` pass.

Run: `rtk python3 skills/scroll-world/scripts/kie_client.py --help`

Expected: exit 0 and list `generate-video` and `wait`.

- [ ] **Step 5: Commit the validated CLI contract**

```bash
rtk git add skills/scroll-world/scripts/kie_client.py skills/scroll-world/tests/test_kie_client.py
rtk git commit -m "feat: define Kie video CLI contracts"
```

---

### Task 2: HTTP transport, retries, and multipart frame upload

**Files:**
- Modify: `skills/scroll-world/scripts/kie_client.py`
- Modify: `skills/scroll-world/tests/test_kie_client.py`

**Interfaces:**
- Consumes: `ValidationError` from Task 1.
- Produces: `HttpResponse(status: int, headers: Mapping[str, str], body: bytes)`, `HttpError`, `UrllibTransport.request(...)`, `request_with_retry(...)`, `encode_multipart_file(...)`, and `upload_frame(...) -> str`.

- [ ] **Step 1: Write failing multipart and retry tests**

```python
import json

def json_response(status, payload):
    return kie_client.HttpResponse(
        status=status,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode("utf-8"),
    )

def binary_response(status, body):
    return kie_client.HttpResponse(
        status=status,
        headers={"content-type": "application/octet-stream"},
        body=body,
    )

class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, headers, body=None, timeout=60):
        self.requests.append({
            "method": method,
            "url": url,
            "headers": dict(headers),
            "body": body,
            "timeout": timeout,
        })
        return self.responses.pop(0)

class UploadTests(unittest.TestCase):
    def test_upload_returns_download_url_and_never_serializes_key(self):
        transport = FakeTransport([
            json_response(200, {
                "success": True,
                "code": 200,
                "data": {"downloadUrl": "https://tempfile.example/start.png"},
            })
        ])
        url = kie_client.upload_frame(
            Path(self.image_path), "secret-value", transport, sleeper=lambda _: None
        )
        self.assertEqual(url, "https://tempfile.example/start.png")
        request = transport.requests[0]
        self.assertEqual(request["url"], kie_client.UPLOAD_URL)
        self.assertIn(b"Content-Disposition: form-data", request["body"])
        self.assertNotIn(b"secret-value", request["body"])

    def test_429_then_success_retries_once(self):
        transport = FakeTransport([
            json_response(429, {"msg": "rate limited"}),
            json_response(200, {"success": True, "code": 200,
                                "data": {"downloadUrl": "https://tempfile.example/a.png"}}),
        ])
        sleeps = []
        url = kie_client.upload_frame(
            Path(self.image_path), "secret", transport, sleeper=sleeps.append,
            randomizer=lambda: 0.0
        )
        self.assertEqual(url, "https://tempfile.example/a.png")
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(len(sleeps), 1)

    def test_401_is_not_retried(self):
        transport = FakeTransport([json_response(401, {"msg": "unauthorized"})])
        with self.assertRaisesRegex(kie_client.HttpError, "authentication"):
            kie_client.upload_frame(Path(self.image_path), "secret", transport)
        self.assertEqual(len(transport.requests), 1)
```

- [ ] **Step 2: Run upload tests and confirm failure**

Run: `rtk python3 skills/scroll-world/tests/test_kie_client.py UploadTests -v`

Expected: failures for missing transport, retry, multipart, and upload functions.

- [ ] **Step 3: Implement bounded retry and multipart encoding**

```python
import hashlib
import mimetypes
import random
import time
import uuid

UPLOAD_URL = "https://kieai.redpandaai.co/api/file-stream-upload"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

def encode_multipart_file(path, upload_path="images/scroll-world"):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    remote_name = f"{path.stem}-{digest}{path.suffix.lower()}"
    boundary = f"----scrollworld-{uuid.uuid4().hex}"
    fields = [("uploadPath", upload_path), ("fileName", remote_name)]
    chunks = []
    for name, value in fields:
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode(),
            b"\r\n",
        ])
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{remote_name}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return boundary, b"".join(chunks)

def request_with_retry(operation, sleeper=time.sleep, randomizer=random.random,
                       max_attempts=4):
    for attempt in range(max_attempts):
        response = operation()
        if response.status not in RETRYABLE_STATUS:
            return response
        if attempt == max_attempts - 1:
            return response
        delay = min(30.0, 3.0 * (2 ** attempt)) + randomizer()
        sleeper(delay)
```

Complete multipart generation with file MIME detection, exact `Authorization: Bearer ...`, and schema checks for `success`, `code`, and `data.downloadUrl`. Map 400, 401, and 402 to focused messages and redact secret strings from raised exceptions.

- [ ] **Step 4: Run upload and regression tests**

Run: `rtk python3 skills/scroll-world/tests/test_kie_client.py UploadTests -v`

Expected: all `UploadTests` pass.

Run: `rtk python3 -m unittest discover -s skills/scroll-world/tests -v`

Expected: all tests pass with no network access.

- [ ] **Step 5: Commit the upload transport**

```bash
rtk git add skills/scroll-world/scripts/kie_client.py skills/scroll-world/tests/test_kie_client.py
rtk git commit -m "feat: upload local frames to Kie"
```

---

### Task 3: Task submission, atomic manifests, and resume identity

**Files:**
- Modify: `skills/scroll-world/scripts/kie_client.py`
- Modify: `skills/scroll-world/tests/test_kie_client.py`

**Interfaces:**
- Consumes: `GenerationConfig`, `build_task_payload`, `upload_frame`, `request_with_retry`.
- Produces: `manifest_path_for(output: Path) -> Path`, `write_manifest_atomic(path, data)`, `load_manifest(path)`, and `create_task(payload, api_key, transport) -> str`.

- [ ] **Step 1: Write failing task and manifest tests**

```python
class ManifestTests(unittest.TestCase):
    def test_create_task_persists_id_before_polling(self):
        transport = FakeTransport([
            json_response(200, {"code": 200, "msg": "success",
                                "data": {"taskId": "task_bytedance_123"}})
        ])
        task_id = kie_client.create_task(
            {"model": kie_client.DEFAULT_MODEL, "input": {"prompt": "move"}},
            "secret", transport
        )
        self.assertEqual(task_id, "task_bytedance_123")

    def test_manifest_write_is_atomic_and_has_no_secret(self):
        path = Path(self.tempdir.name) / "clip.mp4.kie.json"
        data = {"schemaVersion": 1, "taskId": "task_1", "state": "waiting"}
        kie_client.write_manifest_atomic(path, data)
        self.assertEqual(json.loads(path.read_text()), data)
        self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())
        self.assertNotIn("secret", path.read_text())

    def test_existing_manifest_with_task_id_blocks_resubmission(self):
        path = Path(self.tempdir.name) / "clip.mp4.kie.json"
        kie_client.write_manifest_atomic(path, {"schemaVersion": 1,
                                                "taskId": "task_existing"})
        with self.assertRaisesRegex(kie_client.ValidationError, "wait"):
            kie_client.ensure_new_generation(path)
```

- [ ] **Step 2: Run manifest tests and confirm failure**

Run: `rtk python3 skills/scroll-world/tests/test_kie_client.py ManifestTests -v`

Expected: failures for missing task and manifest functions.

- [ ] **Step 3: Implement submission and atomic manifest state**

```python
CREATE_TASK_URL = "https://api.kie.ai/api/v1/jobs/createTask"
MANIFEST_SCHEMA_VERSION = 1

def manifest_path_for(output):
    return Path(str(output) + ".kie.json")

def write_manifest_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)
```

`create_task` must send JSON with `Content-Type: application/json`, validate top-level `code == 200`, and require a non-empty string `data.taskId`. The orchestration path must write model, non-secret parameters, local input paths, uploaded URLs, task ID, `state: waiting`, output path, and UTC timestamps immediately after submission.

- [ ] **Step 4: Run manifest and full tests**

Run: `rtk python3 skills/scroll-world/tests/test_kie_client.py ManifestTests -v`

Expected: all `ManifestTests` pass.

Run: `rtk python3 -m unittest discover -s skills/scroll-world/tests -v`

Expected: all tests pass.

- [ ] **Step 5: Commit resumable task identity**

```bash
rtk git add skills/scroll-world/scripts/kie_client.py skills/scroll-world/tests/test_kie_client.py
rtk git commit -m "feat: persist resumable Kie tasks"
```

---

### Task 4: Polling, failure reporting, result parsing, and timeouts

**Files:**
- Modify: `skills/scroll-world/scripts/kie_client.py`
- Modify: `skills/scroll-world/tests/test_kie_client.py`

**Interfaces:**
- Consumes: manifest functions and retry transport.
- Produces: `TaskFailedError`, `TaskTimeoutError`, `parse_task_record(data) -> tuple[str, str | None]`, and `wait_for_task(task_id, ...) -> str`.

- [ ] **Step 1: Write failing polling tests**

```python
def task_record(state, progress=0, result_json=None, fail_code="", fail_msg=""):
    data = {
        "taskId": "task_1",
        "state": state,
        "progress": progress,
        "resultJson": result_json,
        "failCode": fail_code,
        "failMsg": fail_msg,
    }
    return json_response(200, {"code": 200, "msg": "success", "data": data})

class PollingTests(unittest.TestCase):
    def test_waiting_generating_success_returns_nested_result_url(self):
        transport = FakeTransport([
            task_record("waiting"),
            task_record("generating", progress=70),
            task_record("success", result_json=json.dumps({
                "resultUrls": ["https://result.example/clip.mp4"]
            })),
        ])
        url = kie_client.wait_for_task(
            "task_1", "secret", transport, sleeper=lambda _: None,
            randomizer=lambda: 0.0, timeout_seconds=60
        )
        self.assertEqual(url, "https://result.example/clip.mp4")

    def test_fail_state_reports_provider_message(self):
        transport = FakeTransport([
            task_record("fail", fail_code="CONTENT", fail_msg="input rejected")
        ])
        with self.assertRaisesRegex(kie_client.TaskFailedError,
                                    "CONTENT.*input rejected"):
            kie_client.wait_for_task("task_1", "secret", transport)

    def test_malformed_result_json_is_schema_error(self):
        transport = FakeTransport([task_record("success", result_json="not-json")])
        with self.assertRaises(kie_client.SchemaError):
            kie_client.wait_for_task("task_1", "secret", transport)
```

- [ ] **Step 2: Run polling tests and confirm failure**

Run: `rtk python3 skills/scroll-world/tests/test_kie_client.py PollingTests -v`

Expected: failures for missing polling and result parsing behavior.

- [ ] **Step 3: Implement documented state handling and timeout**

```python
TASK_INFO_URL = "https://api.kie.ai/api/v1/jobs/recordInfo"
ACTIVE_STATES = {"waiting", "queuing", "generating"}

def parse_task_record(data):
    state = data.get("state")
    if state in ACTIVE_STATES:
        return state, None
    if state == "fail":
        raise TaskFailedError(f"{data.get('failCode', '')}: {data.get('failMsg', '')}")
    if state != "success":
        raise SchemaError(f"unexpected task state: {state!r}")
    try:
        result = json.loads(data["resultJson"])
        url = result["resultUrls"][0]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise SchemaError("successful task did not contain resultUrls[0]") from exc
    return state, url
```

`wait_for_task` must use monotonic time, persist each observed state and progress in the manifest when provided, use backoff from about 3 to 30 seconds, and preserve state when timeout or `KeyboardInterrupt` occurs. The error output must include the exact `wait --manifest ... --output ...` resume command.

- [ ] **Step 4: Run polling and full tests**

Run: `rtk python3 skills/scroll-world/tests/test_kie_client.py PollingTests -v`

Expected: all `PollingTests` pass.

Run: `rtk python3 -m unittest discover -s skills/scroll-world/tests -v`

Expected: all tests pass without sleeping or network access.

- [ ] **Step 5: Commit task polling**

```bash
rtk git add skills/scroll-world/scripts/kie_client.py skills/scroll-world/tests/test_kie_client.py
rtk git commit -m "feat: poll Kie video tasks"
```

---

### Task 5: Atomic download, FFprobe validation, and end-to-end CLI orchestration

**Files:**
- Modify: `skills/scroll-world/scripts/kie_client.py`
- Modify: `skills/scroll-world/tests/test_kie_client.py`
- Create: `.env.example`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: all Task 1-4 interfaces.
- Produces: `download_result(url, output, transport)`, `probe_video(path, runner=subprocess.run)`, `run_generate(config, ...)`, `run_wait(...)`, and `main(argv=None) -> int`.

- [ ] **Step 1: Write failing download, probe, resume, and redaction tests**

```python
class EndToEndTests(unittest.TestCase):
    def test_download_uses_part_file_then_replaces_output(self):
        output = Path(self.tempdir.name) / "clip.mp4"
        transport = FakeTransport([binary_response(200, b"fake-mp4")])
        kie_client.download_result(
            "https://result/clip.mp4", output, transport,
            probe=lambda _: {"codec_name": "h264"}
        )
        self.assertEqual(output.read_bytes(), b"fake-mp4")
        self.assertFalse(Path(str(output) + ".part").exists())

    def test_wait_resumes_existing_task_without_create_request(self):
        manifest = Path(self.tempdir.name) / "clip.mp4.kie.json"
        output = Path(self.tempdir.name) / "clip.mp4"
        kie_client.write_manifest_atomic(manifest, {
            "schemaVersion": 1,
            "taskId": "task_existing",
            "state": "generating",
            "output": str(output),
        })
        transport = FakeTransport([
            task_record("success", result_json=json.dumps({
                "resultUrls": ["https://result/clip.mp4"]
            })),
            binary_response(200, b"fake-mp4"),
        ])
        kie_client.run_wait(manifest, output, "secret", transport,
                            probe=lambda _: {"codec_name": "h264"})
        self.assertFalse(any("createTask" in item["url"]
                             for item in transport.requests))

    def test_error_text_redacts_api_key(self):
        text = kie_client.redact("Authorization: Bearer secret-value", "secret-value")
        self.assertNotIn("secret-value", text)
```

- [ ] **Step 2: Run end-to-end tests and confirm failure**

Run: `rtk python3 skills/scroll-world/tests/test_kie_client.py EndToEndTests -v`

Expected: failures for missing download, probe, orchestration, and redaction functions.

- [ ] **Step 3: Implement download, probe, and CLI exit behavior**

```python
def probe_video(path, runner=subprocess.run):
    completed = runner([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,duration",
        "-of", "json", str(path),
    ], check=True, capture_output=True, text=True)
    streams = json.loads(completed.stdout).get("streams", [])
    if not streams or not streams[0].get("codec_name"):
        raise MediaValidationError("download did not contain a video stream")
    return streams[0]

def download_result(url, output, transport, probe=probe_video):
    part = Path(str(output) + ".part")
    response = request_with_retry(lambda: transport.request("GET", url, {}, None))
    require_successful_http(response)
    part.write_bytes(response.body)
    probe(part)
    os.replace(part, output)
```

Complete `run_generate` as validate → upload one or two frames → build payload → submit → persist task ID → poll → download to `.part` → probe `.part` → atomically replace final output → finalize manifest. `main` prints progress to stderr, one final JSON object to stdout, and returns a distinct nonzero status for expected client failures.

- [ ] **Step 4: Add safe environment examples and ignore rules**

Create `.env.example` exactly as:

```dotenv
MEDIA_PROVIDER=kie
KIE_API_KEY=
KIE_MODEL=bytedance/seedance-2-fast
STILLS_SOURCE=codex
```

Append these rules to `.gitignore`:

```gitignore
.env
.env.*
!.env.example
*.kie.json
*.mp4.part
skills/scroll-world/.smoke/
```

- [ ] **Step 5: Run the complete automated suite and syntax checks**

Run: `rtk python3 -m unittest discover -s skills/scroll-world/tests -v`

Expected: all tests pass without network access.

Run: `rtk python3 -m py_compile skills/scroll-world/scripts/kie_client.py skills/scroll-world/tests/test_kie_client.py`

Expected: exit 0 with no output.

Run: `rtk git diff --check`

Expected: exit 0 with no whitespace errors.

- [ ] **Step 6: Commit the complete client**

```bash
rtk git add .env.example .gitignore skills/scroll-world/scripts/kie_client.py skills/scroll-world/tests/test_kie_client.py
rtk git commit -m "feat: complete resumable Kie video client"
```

---

### Task 6: Kie pipeline and Codex image-generation documentation

**Files:**
- Create: `skills/scroll-world/references/pipeline-kie.md`
- Modify: `skills/scroll-world/SKILL.md`
- Modify: `skills/scroll-world/references/pipeline.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: exact CLI arguments implemented in Task 5.
- Produces: the user-facing default workflow `STILLS_SOURCE=codex` + `MEDIA_PROVIDER=kie`; retains the Higgsfield fallback.

- [ ] **Step 1: Write a documentation contract test**

Add this test class to `test_kie_client.py`:

```python
class DocumentationTests(unittest.TestCase):
    def test_default_provider_and_commands_are_documented(self):
        root = Path(__file__).resolve().parents[3]
        readme = (root / "README.md").read_text(encoding="utf-8")
        skill = (root / "skills/scroll-world/SKILL.md").read_text(encoding="utf-8")
        pipeline = (root / "skills/scroll-world/references/pipeline-kie.md").read_text(
            encoding="utf-8"
        )
        for text in (readme, skill, pipeline):
            self.assertIn("bytedance/seedance-2-fast", text)
        self.assertIn("STILLS_SOURCE=codex", skill)
        self.assertIn("MEDIA_PROVIDER=kie", skill)
        self.assertIn("generate-video", pipeline)
        self.assertIn("--end-image", pipeline)
        self.assertIn("wait --manifest", pipeline)
```

- [ ] **Step 2: Run the documentation test and confirm failure**

Run: `rtk python3 skills/scroll-world/tests/test_kie_client.py DocumentationTests -v`

Expected: failure because `pipeline-kie.md` is absent and the default-provider copy is not present.

- [ ] **Step 3: Write the Kie pipeline with exact executable examples**

`pipeline-kie.md` must include:

```bash
MEDIA_PROVIDER=kie
STILLS_SOURCE=codex
KIE_MODEL=bytedance/seedance-2-fast

python3 "$SKILL/scripts/kie_client.py" generate-video \
  --prompt-file "$WORK/dive_$name.txt" \
  --start-image "$WORK/start_$name.png" \
  --aspect-ratio 16:9 --resolution 720p --duration 15 \
  --output "$WORK/dive_$name.mp4"

python3 "$SKILL/scripts/kie_client.py" generate-video \
  --prompt-file "$WORK/conn_$index.txt" \
  --start-image "$WORK/last_$previous.png" \
  --end-image "$WORK/first_$next.png" \
  --aspect-ratio 16:9 --resolution 720p --duration 15 \
  --output "$WORK/conn_$index.mp4"
```

Also include `.env.local` sourcing, credit warning, detached execution, resume command, FFmpeg boundary extraction, audio removal, scrub-friendly encoding, native 9:16 canvas and chain, temporary URL warning, and the rule that connectors use rendered boundary frames rather than newly generated images.

- [ ] **Step 4: Update skill and README routing**

In `SKILL.md`, preserve the interview and seam rules but change bootstrap and generation routing so:

```text
Codex default stills: built-in image_gen, copied into the project workspace
Default video provider: Kie.ai bytedance/seedance-2-fast
Fallback video provider: Higgsfield pipeline.md
```

Require the agent to inspect every generated still, normalize landscape stills to 16:9 and mobile stills to 9:16, and use FFmpeg-extracted frames for every seam. In `README.md`, update requirements and add a provider matrix. Add a heading at the top of the existing `pipeline.md` identifying it as the Higgsfield fallback without altering its commands.

- [ ] **Step 5: Run documentation and full tests**

Run: `rtk python3 skills/scroll-world/tests/test_kie_client.py DocumentationTests -v`

Expected: documentation contract passes.

Run: `rtk python3 -m unittest discover -s skills/scroll-world/tests -v`

Expected: all tests pass.

- [ ] **Step 6: Commit provider documentation**

```bash
rtk git add README.md skills/scroll-world/SKILL.md skills/scroll-world/references/pipeline.md skills/scroll-world/references/pipeline-kie.md skills/scroll-world/tests/test_kie_client.py
rtk git commit -m "docs: make Kie the default video workflow"
```

---

### Task 7: Real Codex-to-Kie smoke test

**Files:**
- Runtime only: `skills/scroll-world/.smoke/scene-source.png`
- Runtime only: `skills/scroll-world/.smoke/scene-16x9.png`
- Runtime only: `skills/scroll-world/.smoke/prompt.txt`
- Runtime only: `skills/scroll-world/.smoke/clip.mp4`
- Runtime only: `skills/scroll-world/.smoke/clip.mp4.kie.json`

**Interfaces:**
- Consumes: Codex built-in `image_gen`, `kie_client.py generate-video`, Kie credits, and FFprobe.
- Produces: verified non-committed smoke-test media and a concise non-secret test report.

- [ ] **Step 1: Confirm secret and credit prerequisites without printing the key**

The user creates `.env.local` locally with `KIE_API_KEY` and confirms it is ready. Load it without echoing values, then run:

```bash
rtk zsh -c 'set -a; source .env.local; test -n "$KIE_API_KEY"'
```

Expected: exit 0 and no output. If it fails, stop before generation and ask the user to set the variable locally; never ask them to paste the key into chat.

- [ ] **Step 2: Generate and inspect one scene still with built-in image generation**

Invoke the `imagegen` skill in built-in mode with this exact production-oriented request:

```text
Use case: stylized-concept
Asset type: Scroll World smoke-test opening frame
Primary request: a small isometric clay diorama of a warm futuristic parcel sorting hub
Scene/backdrop: clean pale cream studio background
Style/medium: soft matte low-poly clay 3D render, rounded toy-model forms, tilt-shift miniature
Composition/framing: wide centered composition with generous background around the island
Lighting/mood: gentle warm studio light, welcoming and calm
Color palette: cream, terracotta, muted teal, warm amber
Constraints: no text, no letters, no numbers, no logo, no watermark
```

Inspect the output, copy the selected file into `skills/scroll-world/.smoke/scene-source.png`, and report the final prompt and source path as required by the image-generation skill.

- [ ] **Step 3: Normalize the frame and write the motion prompt**

Run:

```bash
rtk ffmpeg -v error -y -i skills/scroll-world/.smoke/scene-source.png \
  -vf "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0xF5EDE0" \
  skills/scroll-world/.smoke/scene-16x9.png
```

Create `skills/scroll-world/.smoke/prompt.txt` with `apply_patch` containing:

```text
Single continuous cinematic camera move, no cuts. Begin high and far, looking down at the whole miniature parcel sorting hub. The camera slowly glides forward and descends toward the central conveyor, with subtle parallax and calm steady motion. Soft matte clay diorama, warm studio light, cream, terracotta, muted teal, and amber palette. No text, no captions.
```

- [ ] **Step 4: Run one paid Kie.ai generation**

Run without printing the environment:

```bash
rtk zsh -c 'set -a; source .env.local; python3 skills/scroll-world/scripts/kie_client.py generate-video \
  --prompt-file skills/scroll-world/.smoke/prompt.txt \
  --start-image skills/scroll-world/.smoke/scene-16x9.png \
  --aspect-ratio 16:9 --resolution 720p --duration 15 \
  --output skills/scroll-world/.smoke/clip.mp4'
```

Expected: progress moves through documented task states, exits 0, prints final non-secret JSON, and creates `clip.mp4` plus its manifest.

- [ ] **Step 5: Verify media and visually inspect representative frames**

Run:

```bash
rtk ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,width,height,duration \
  -of json skills/scroll-world/.smoke/clip.mp4
rtk ffmpeg -v error -y -i skills/scroll-world/.smoke/clip.mp4 \
  -vf "select='eq(n,0)+eq(n,60)'" -vsync 0 \
  skills/scroll-world/.smoke/frame-%02d.png
```

Expected: a decodable video stream, 1280×720 or the provider's documented 720p equivalent, positive duration, and two extracted PNGs. Inspect both PNGs for a valid opening frame and visible camera motion.

- [ ] **Step 6: Run final regression and secret audit**

Run:

```bash
rtk python3 -m unittest discover -s skills/scroll-world/tests -v
rtk git diff --check
rtk git status --short
rtk git grep -n "KIE_API_KEY=" -- ':!.env.example'
```

Expected: tests pass; diff check passes; smoke artifacts and `.env.local` are absent from Git status; secret grep returns no tracked credential assignment.

- [ ] **Step 7: Commit only any test-proven documentation corrections**

If the live API exposes a documented field name or accepted value that differs from the implementation, update the client, tests, and docs together with `apply_patch`, rerun the entire suite, and commit those exact tracked files:

```bash
rtk git add skills/scroll-world/scripts/kie_client.py skills/scroll-world/tests/test_kie_client.py skills/scroll-world/references/pipeline-kie.md README.md skills/scroll-world/SKILL.md
rtk git commit -m "fix: align Kie client with live API"
```

If no tracked correction is required, do not create an empty commit.

---

## Final Verification

Run:

```bash
rtk python3 -m unittest discover -s skills/scroll-world/tests -v
rtk python3 -m py_compile skills/scroll-world/scripts/kie_client.py skills/scroll-world/tests/test_kie_client.py
rtk git diff --check
rtk git status --short --branch
rtk git log --oneline --decorate -8
```

Expected:

- all automated tests pass without network access;
- Python compilation succeeds;
- no whitespace errors;
- worktree is clean except for intentionally uncommitted, ignored smoke artifacts;
- branch history contains small commits matching the task boundaries;
- no generated video, manifest, partial file, `.env.local`, or API key is committed.
