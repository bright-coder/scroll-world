# Kie.ai Provider Design

## Summary

Add Kie.ai as the default video-generation provider for Scroll World while retaining the existing Higgsfield pipeline as a fallback. The first supported Kie.ai model is `bytedance/seedance-2-fast`. The integration accepts local first and optional last frame images, uploads them, creates an asynchronous video task, waits for completion, downloads the result, and records enough state to resume without submitting a duplicate paid task.

## Goals

- Generate a video from a local first frame with one command.
- Generate a frame-locked connector from local first and last frames.
- Use `bytedance/seedance-2-fast` by default.
- Poll asynchronous tasks and download the generated MP4 immediately.
- Persist the task ID before polling so interrupted work can resume without spending credits twice.
- Keep the current Higgsfield instructions available as a fallback.
- Test the client without network access and perform one real paid smoke test.

## Non-goals

- A general multi-provider SDK or Node package.
- Support for every model in the Kie.ai marketplace.
- A webhook server; the local CLI uses polling.
- Replacing FFmpeg, the scrub engine, prompt templates, or seam-locking rules.
- Automatically generating all story assets in a single command.

## Repository Structure

```text
skills/scroll-world/
├── scripts/
│   └── kie_client.py
├── tests/
│   └── test_kie_client.py
└── references/
    ├── pipeline-kie.md
    └── pipeline.md
```

`pipeline-kie.md` becomes the default Kie.ai workflow. The existing `pipeline.md` remains the Higgsfield fallback, minimizing upstream merge conflicts. `README.md` and `SKILL.md` will describe Kie.ai as the fork's default video provider and link to both workflows.

## Configuration

The client reads credentials only from the environment:

```text
MEDIA_PROVIDER=kie
KIE_API_KEY=
KIE_MODEL=bytedance/seedance-2-fast
```

`MEDIA_PROVIDER` documents the selected workflow; the Python client itself is explicitly Kie-specific. `KIE_MODEL` is optional and defaults to `bytedance/seedance-2-fast`. The API key is not accepted as a command-line argument, written to a manifest, or included in logs. `.env.local` and other local secret files are ignored by Git; an example file contains names only, never credentials.

## Command Interface

The primary command is:

```bash
python3 scripts/kie_client.py generate-video \
  --prompt-file dive_shop.txt \
  --start-image still_shop.png \
  --aspect-ratio 16:9 \
  --resolution 720p \
  --duration 15 \
  --output dive_shop.mp4
```

A connector supplies an additional `--end-image` argument. The documented default duration is 15 seconds because that value appears in Kie.ai's current Seedance 2 Fast request example. Duration remains configurable and is passed through for server-side validation rather than encoded as an assumed local enum.

The resume command is:

```bash
python3 scripts/kie_client.py wait \
  --manifest dive_shop.mp4.kie.json \
  --output dive_shop.mp4
```

Progress is written to standard error. Machine-readable final status is written to standard output. Exit status is nonzero for validation, authentication, credit, task, timeout, schema, download, and media-validation failures.

## API Contracts

### Upload local frames

The client sends each local frame as multipart form data:

```text
POST https://kieai.redpandaai.co/api/file-stream-upload
```

It reads `data.downloadUrl` from a successful response. Uploaded filenames include a content-derived suffix to avoid accidental overwrite and stale-cache behavior.

### Create a video task

The client submits:

```text
POST https://api.kie.ai/api/v1/jobs/createTask
```

The request uses model `bytedance/seedance-2-fast`. A dive includes `first_frame_url`; a connector includes both `first_frame_url` and `last_frame_url`. It does not include `reference_image_urls`, `reference_video_urls`, or `reference_audio_urls`, because Kie.ai documents those scenarios as mutually exclusive with first/last-frame generation. Audio and web search default to disabled. The client reads `data.taskId` and writes the manifest before beginning to poll.

### Query a task

The client polls:

```text
GET https://api.kie.ai/api/v1/jobs/recordInfo?taskId=<id>
```

Recognized states are `waiting`, `queuing`, `generating`, `success`, and `fail`. On success, the client parses `data.resultJson` as a second JSON document and selects the first URL from `resultUrls`.

## Manifest and Resume Behavior

The manifest is stored next to the requested output as `<output>.kie.json`. It contains:

- schema version;
- model and non-secret generation parameters;
- local input paths and uploaded temporary URLs;
- task ID and latest task state;
- result URL when available;
- output path and timestamps.

The manifest is updated atomically. Once a task ID exists, retries and resume operations query that task rather than creating another. Re-submission requires an explicit new `generate-video` invocation after the user chooses a new output or removes the old manifest.

## Polling and Download Flow

1. Validate the API key, prompt, input files, aspect ratio, resolution, duration, and output path.
2. Upload the first frame and optional last frame.
3. Submit the task and atomically persist its task ID.
4. Poll with bounded exponential backoff and jitter, beginning near three seconds and capping near thirty seconds.
5. Stop at a configurable timeout, preserving the manifest for resume.
6. Parse the result URL on success.
7. Download to `<output>.part`.
8. Validate the downloaded file with `ffprobe`.
9. Atomically rename it to the requested MP4 path.

The CLI does not need a callback URL because it runs locally and has no public webhook receiver.

## Error Handling

- HTTP 400, 401, and 402 fail immediately with a focused request, credential, or credit message.
- HTTP 429, transient network errors, and HTTP 5xx responses receive bounded retries.
- A task in `fail` reports Kie.ai's `failCode` and `failMsg`.
- Missing fields, invalid nested JSON, and unexpected task states are schema errors; the client does not guess a result URL.
- Interruptions and timeouts preserve the manifest and print the resume command.
- Download failures retry the download without resubmitting video generation.
- Media validation failures keep diagnostic state but do not replace an existing valid output.
- Logs and exception messages redact authorization headers and never include `KIE_API_KEY`.

## Testing

### Automated tests

The HTTP transport and sleeper are injected so tests use only Python's standard library and no real network. Tests cover:

- local input validation;
- multipart upload response parsing;
- task request construction for first-frame and first/last-frame modes;
- immediate persistence of the task ID;
- all documented task states;
- nested `resultJson` parsing;
- retryable and non-retryable HTTP errors;
- malformed and incomplete responses;
- timeout and interruption behavior;
- resume without task resubmission;
- atomic download and manifest writes;
- secret redaction;
- `ffprobe` success and failure.

### Live smoke test

After automated tests pass, run one real first-frame video generation using Kie.ai credits. The API key is supplied through an ignored `.env.local` file or the process environment. The test uploads one local image, creates one `bytedance/seedance-2-fast` task, polls it, downloads the MP4, and verifies codec, dimensions, and duration with `ffprobe`. The generated video and local secret file are not committed. The smoke-test report records only non-secret task status and media metadata.

## Documentation Changes

- `README.md`: explain that this fork defaults to Kie.ai and retains Higgsfield as a fallback.
- `SKILL.md`: change bootstrap, budget, model selection, and generation instructions to route to `pipeline-kie.md` by default.
- `pipeline-kie.md`: provide copy-paste examples for dives, connectors, frame extraction, encoding, mobile variants, resume, and troubleshooting.
- Existing `pipeline.md`: add only a clear Higgsfield fallback label if needed; preserve its commands.
- `.env.example`: list safe configuration names with blank values.
- `.gitignore`: exclude `.env`, `.env.*`, generated manifests, test MP4s, and partial downloads while allowing `.env.example`.

## Acceptance Criteria

- The complete automated test suite passes without network access.
- A first-frame task produces a valid local MP4 in a real Kie.ai smoke test.
- A connector request is verified by automated request-contract tests with both uploaded frame URLs.
- Interrupting after task creation and running `wait` resumes the same task ID.
- No API key appears in Git history, logs, manifests, test fixtures, or process arguments.
- Higgsfield instructions remain usable and clearly labeled as fallback.
