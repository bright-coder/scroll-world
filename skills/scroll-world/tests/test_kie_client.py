import os
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
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


class ValidationTests(unittest.TestCase):
    def assert_invalid(self, config, message):
        with self.assertRaisesRegex(kie_client.ValidationError, message):
            kie_client.validate_generation(config)

    def test_missing_and_non_regular_input_files_are_rejected(self):
        config = make_config()
        directory = config.prompt_file.parent
        cases = (
            (replace(config, prompt_file=directory / "missing.txt"), "prompt file"),
            (replace(config, start_image=directory / "missing.png"), "start image"),
            (replace(config, start_image=directory), "start image"),
            (replace(config, end_image=directory / "missing-end.png"), "end image"),
            (replace(config, end_image=directory), "end image"),
        )
        for invalid_config, message in cases:
            with self.subTest(config=invalid_config):
                self.assert_invalid(invalid_config, message)

    def test_empty_prompt_is_rejected(self):
        config = make_config()
        config.prompt_file.write_text(" \n\t ", encoding="utf-8")
        self.assert_invalid(config, "must not be empty")

    def test_unsupported_aspect_ratio_is_rejected(self):
        self.assert_invalid(replace(make_config(), aspect_ratio="1:1"), "aspect ratio")

    def test_unsupported_resolution_is_rejected(self):
        self.assert_invalid(replace(make_config(), resolution="1080p"), "resolution")

    def test_non_positive_duration_is_rejected(self):
        for duration in (0, -1):
            with self.subTest(duration=duration):
                self.assert_invalid(
                    replace(make_config(), duration=duration), "duration"
                )

    def test_non_positive_timeout_is_rejected(self):
        for timeout_seconds in (0, -1):
            with self.subTest(timeout_seconds=timeout_seconds):
                self.assert_invalid(
                    replace(make_config(), timeout_seconds=timeout_seconds), "timeout"
                )

    def test_directory_output_is_rejected(self):
        config = make_config()
        self.assert_invalid(replace(config, output=config.prompt_file.parent), "output")

    def test_kie_model_environment_variable_overrides_default(self):
        config = make_config()
        with mock.patch.dict(os.environ, {"KIE_MODEL": "custom/model"}, clear=True):
            args = kie_client.parse_args(
                [
                    "generate-video",
                    "--prompt-file",
                    str(config.prompt_file),
                    "--start-image",
                    str(config.start_image),
                    "--output",
                    str(config.output),
                ]
            )
        self.assertEqual(args.config.model, "custom/model")


def json_response(status, payload):
    return kie_client.HttpResponse(
        status=status,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode("utf-8"),
    )


def binary_response(status, body):
    return kie_client.HttpResponse(
        status=status,
        headers={"content-type": "video/mp4"},
        body=body,
    )


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, headers, body=None, timeout=60):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout": timeout,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class RetryTests(unittest.TestCase):
    def test_transport_wraps_url_and_timeout_failures_as_transient(self):
        failures = (
            kie_client.URLError("temporary DNS failure"),
            TimeoutError("request timed out"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(kie_client, "urlopen", side_effect=failure):
                    with self.assertRaisesRegex(
                        kie_client.TransientNetworkError,
                        "network error while contacting Kie.ai",
                    ):
                        kie_client.UrllibTransport().request(
                            "GET", "https://example.invalid", {}
                        )

    def test_transient_network_error_is_retried_for_safe_operation(self):
        attempts = []
        sleeps = []

        def operation():
            attempts.append(None)
            if len(attempts) == 1:
                raise kie_client.TransientNetworkError("temporary timeout")
            return json_response(200, {"ok": True})

        response = kie_client.request_with_retry(
            operation,
            sleeper=sleeps.append,
            randomizer=lambda: 0.0,
            max_attempts=2,
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(sleeps, [3.0])

    def test_transient_network_error_is_raised_after_bounded_attempts(self):
        attempts = []

        def operation():
            attempts.append(None)
            raise kie_client.TransientNetworkError("temporary timeout")

        with self.assertRaisesRegex(
            kie_client.TransientNetworkError, "temporary timeout"
        ):
            kie_client.request_with_retry(
                operation,
                sleeper=lambda _: None,
                randomizer=lambda: 0.0,
                max_attempts=3,
            )

        self.assertEqual(len(attempts), 3)

    def test_non_positive_or_non_integer_max_attempts_are_rejected(self):
        for max_attempts in (0, -1, True, 1.5, "4"):
            with self.subTest(max_attempts=max_attempts):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    kie_client.request_with_retry(
                        lambda: json_response(200, {}),
                        max_attempts=max_attempts,
                    )


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.image_path = self.directory / "start.png"
        self.image_path.write_bytes(b"png-frame")

    def test_upload_returns_download_url_and_never_serializes_key(self):
        transport = FakeTransport(
            [
                json_response(
                    200,
                    {
                        "success": True,
                        "code": 200,
                        "data": {"downloadUrl": "https://tempfile.example/start.png"},
                    },
                )
            ]
        )
        url = kie_client.upload_frame(
            self.image_path, "secret-value", transport, sleeper=lambda _: None
        )
        self.assertEqual(url, "https://tempfile.example/start.png")
        request = transport.requests[0]
        self.assertEqual(request["url"], kie_client.UPLOAD_URL)
        self.assertIn(b"Content-Disposition: form-data", request["body"])
        self.assertNotIn(b"secret-value", request["body"])

    def test_429_then_success_retries_once(self):
        transport = FakeTransport(
            [
                json_response(429, {"msg": "rate limited"}),
                json_response(
                    200,
                    {
                        "success": True,
                        "code": 200,
                        "data": {"downloadUrl": "https://tempfile.example/a.png"},
                    },
                ),
            ]
        )
        sleeps = []
        url = kie_client.upload_frame(
            self.image_path,
            "secret",
            transport,
            sleeper=sleeps.append,
            randomizer=lambda: 0.0,
        )
        self.assertEqual(url, "https://tempfile.example/a.png")
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(len(sleeps), 1)

    def test_401_is_not_retried(self):
        transport = FakeTransport([json_response(401, {"msg": "unauthorized"})])
        with self.assertRaisesRegex(kie_client.HttpError, "authentication"):
            kie_client.upload_frame(self.image_path, "secret", transport)
        self.assertEqual(len(transport.requests), 1)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_create_task_persists_id_before_polling(self):
        transport = FakeTransport(
            [
                json_response(
                    200,
                    {
                        "code": 200,
                        "msg": "success",
                        "data": {"taskId": "task_bytedance_123"},
                    },
                )
            ]
        )
        task_id = kie_client.create_task(
            {"model": kie_client.DEFAULT_MODEL, "input": {"prompt": "move"}},
            "secret",
            transport,
        )
        self.assertEqual(task_id, "task_bytedance_123")
        request = transport.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["url"], kie_client.CREATE_TASK_URL)
        self.assertEqual(request["headers"]["Content-Type"], "application/json")
        self.assertEqual(request["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(
            json.loads(request["body"]),
            {"model": kie_client.DEFAULT_MODEL, "input": {"prompt": "move"}},
        )
        self.assertNotIn(b"secret", request["body"])

    def test_create_task_rejects_a_non_object_response(self):
        transport = FakeTransport([json_response(200, [])])
        with self.assertRaisesRegex(kie_client.HttpError, "response schema"):
            kie_client.create_task(
                {"model": kie_client.DEFAULT_MODEL}, "secret", transport
            )

    def test_manifest_write_is_atomic_and_has_no_secret(self):
        path = Path(self.tempdir.name) / "clip.mp4.kie.json"
        data = {"schemaVersion": 1, "taskId": "task_1", "state": "waiting"}
        kie_client.write_manifest_atomic(path, data)
        self.assertEqual(json.loads(path.read_text()), data)
        self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())
        self.assertFalse(list(path.parent.glob(path.name + ".*.tmp")))
        self.assertNotIn("secret", path.read_text())

    def test_existing_manifest_with_task_id_blocks_resubmission(self):
        path = Path(self.tempdir.name) / "clip.mp4.kie.json"
        kie_client.write_manifest_atomic(
            path, {"schemaVersion": 1, "taskId": "task_existing"}
        )
        with self.assertRaisesRegex(kie_client.ValidationError, "wait"):
            kie_client.ensure_new_generation(path)

    def test_reservation_is_exclusive_and_blocks_without_task_id(self):
        config = replace(make_config(), output=Path(self.tempdir.name) / "clip.mp4")

        reservation_id = kie_client.reserve_generation(config)

        manifest_path = kie_client.manifest_path_for(config.output)
        manifest = kie_client.load_manifest(manifest_path)
        self.assertEqual(manifest["reservationId"], reservation_id)
        self.assertEqual(manifest["state"], "reserved")
        self.assertIsNone(manifest["taskId"])
        with self.assertRaisesRegex(kie_client.ValidationError, "reservation.*wait"):
            kie_client.reserve_generation(config)

    def test_run_generate_reserves_manifest_before_upload_and_cleans_definite_failure(self):
        config = replace(make_config(), output=Path(self.tempdir.name) / "clip.mp4")
        manifest_path = kie_client.manifest_path_for(config.output)

        class RejectingUploadTransport:
            def __init__(self):
                self.requests = []
                self.saw_reservation = False

            def request(self, method, url, headers, body=None, timeout=60):
                self.requests.append(url)
                manifest = kie_client.load_manifest(manifest_path)
                self.saw_reservation = (
                    manifest is not None
                    and manifest.get("state") == "reserved"
                    and manifest.get("taskId") is None
                )
                return json_response(400, {"msg": "bad upload"})

        transport = RejectingUploadTransport()
        with self.assertRaisesRegex(kie_client.HttpError, "upload request"):
            kie_client.run_generate(config, "secret", transport)

        self.assertTrue(transport.saw_reservation)
        self.assertEqual(len(transport.requests), 1)
        self.assertFalse(manifest_path.exists())

    def test_existing_reservation_blocks_run_before_any_upload(self):
        config = replace(make_config(), output=Path(self.tempdir.name) / "clip.mp4")
        manifest_path = kie_client.manifest_path_for(config.output)
        kie_client.write_manifest_atomic(
            manifest_path,
            {
                "schemaVersion": 1,
                "reservationId": "another-process",
                "state": "reserved",
                "taskId": None,
            },
        )
        transport = FakeTransport([])

        with self.assertRaisesRegex(kie_client.ValidationError, "reservation.*wait"):
            kie_client.run_generate(config, "secret", transport)

        self.assertEqual(transport.requests, [])

    def test_manifest_preflight_failure_prevents_upload_or_submission(self):
        config = replace(make_config(), output=Path(self.tempdir.name) / "clip.mp4")
        transport = FakeTransport([])

        with mock.patch.object(
            kie_client,
            "reserve_generation",
            side_effect=OSError("manifest destination is read-only"),
        ):
            with self.assertRaisesRegex(OSError, "read-only"):
                kie_client.run_generate(config, "secret", transport)

        self.assertEqual(transport.requests, [])

    def test_ambiguous_create_failure_preserves_uncertain_reservation(self):
        config = replace(make_config(), output=Path(self.tempdir.name) / "clip.mp4")
        transport = FakeTransport(
            [
                json_response(
                    200,
                    {
                        "success": True,
                        "code": 200,
                        "data": {"downloadUrl": "https://files/start.png"},
                    },
                ),
                kie_client.TransientNetworkError("connection lost after send"),
            ]
        )

        with self.assertRaisesRegex(
            kie_client.UncertainSubmissionError, "not retried.*duplicate spend"
        ):
            kie_client.run_generate(config, "secret", transport)

        manifest_path = kie_client.manifest_path_for(config.output)
        manifest = kie_client.load_manifest(manifest_path)
        self.assertEqual(manifest["state"], "submission_uncertain")
        self.assertIsNone(manifest["taskId"])
        self.assertIn("not retried", manifest["lastError"])
        request_count = len(transport.requests)
        with self.assertRaises(kie_client.ValidationError) as raised:
            kie_client.run_generate(config, "secret", transport)
        message = str(raised.exception).lower()
        self.assertIn("retain", message)
        self.assertIn("manifest", message)
        self.assertRegex(message, "task history|support")
        self.assertNotIn("use wait", message)
        self.assertNotIn("new output", message)
        self.assertEqual(len(transport.requests), request_count)

    def test_definitely_rejected_create_cleans_reservation(self):
        config = replace(make_config(), output=Path(self.tempdir.name) / "clip.mp4")
        manifest_path = kie_client.manifest_path_for(config.output)

        class InspectingTransport(FakeTransport):
            def __init__(self, responses):
                super().__init__(responses)
                self.states_at_request = []

            def request(self, *args, **kwargs):
                manifest = kie_client.load_manifest(manifest_path)
                self.states_at_request.append(
                    manifest.get("state") if manifest is not None else None
                )
                return super().request(*args, **kwargs)

        transport = InspectingTransport(
            [
                json_response(
                    200,
                    {
                        "success": True,
                        "code": 200,
                        "data": {"downloadUrl": "https://files/start.png"},
                    },
                ),
                json_response(400, {"msg": "bad task request"}),
            ]
        )

        with self.assertRaisesRegex(kie_client.HttpError, "request was rejected"):
            kie_client.run_generate(config, "secret", transport)

        self.assertEqual(transport.states_at_request, ["reserved", "reserved"])
        self.assertFalse(manifest_path.exists())

    def test_create_task_does_not_retry_a_retryable_failure(self):
        transport = FakeTransport([json_response(503, {"msg": "try later"})])
        with self.assertRaisesRegex(kie_client.HttpError, "not retried"):
            kie_client.create_task(
                {"model": kie_client.DEFAULT_MODEL}, "secret", transport
            )
        self.assertEqual(len(transport.requests), 1)

    def test_create_task_does_not_retry_transient_network_failure(self):
        class FailingTransport:
            def __init__(self):
                self.requests = 0

            def request(self, *_args, **_kwargs):
                self.requests += 1
                raise kie_client.TransientNetworkError("temporary timeout")

        transport = FailingTransport()
        with self.assertRaisesRegex(
            kie_client.HttpError, "not retried.*duplicate spend"
        ):
            kie_client.create_task(
                {"model": kie_client.DEFAULT_MODEL}, "secret", transport
            )
        self.assertEqual(transport.requests, 1)

    def test_create_task_has_focused_non_retryable_http_messages(self):
        expectations = {
            400: "request.*rejected",
            401: "authentication",
            402: "credits",
        }
        for status, message in expectations.items():
            with self.subTest(status=status):
                transport = FakeTransport([json_response(status, {})])
                with self.assertRaisesRegex(
                    kie_client.HttpError, rf"{message}.*not retried"
                ):
                    kie_client.create_task(
                        {"model": kie_client.DEFAULT_MODEL}, "secret", transport
                    )
                self.assertEqual(len(transport.requests), 1)

    def test_submit_generation_writes_complete_waiting_manifest_immediately(self):
        config = replace(make_config(), output=Path(self.tempdir.name) / "clip.mp4")
        transport = FakeTransport(
            [
                json_response(
                    200, {"code": 200, "data": {"taskId": "task_submitted"}}
                )
            ]
        )

        task_id = kie_client.submit_generation(
            config,
            "https://files.example/start.png",
            "https://files.example/end.png",
            "secret-value",
            transport,
        )

        manifest_path = kie_client.manifest_path_for(config.output)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(task_id, "task_submitted")
        self.assertEqual(manifest["schemaVersion"], kie_client.MANIFEST_SCHEMA_VERSION)
        self.assertEqual(manifest["model"], config.model)
        self.assertEqual(manifest["parameters"], {
            "aspectRatio": config.aspect_ratio,
            "duration": config.duration,
            "resolution": config.resolution,
            "timeoutSeconds": config.timeout_seconds,
        })
        self.assertEqual(manifest["localInputs"], {
            "promptFile": str(config.prompt_file),
            "startImage": str(config.start_image),
            "endImage": None,
        })
        self.assertEqual(manifest["uploadedUrls"], {
            "firstFrame": "https://files.example/start.png",
            "lastFrame": "https://files.example/end.png",
        })
        self.assertEqual(manifest["taskId"], "task_submitted")
        self.assertEqual(manifest["state"], "waiting")
        self.assertEqual(manifest["outputPath"], str(config.output))
        self.assertTrue(manifest["createdAt"].endswith("+00:00"))
        self.assertGreaterEqual(manifest["updatedAt"], manifest["createdAt"])
        self.assertNotIn("secret-value", manifest_path.read_text(encoding="utf-8"))


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
        transport = FakeTransport(
            [
                task_record("waiting"),
                task_record("generating", progress=70),
                task_record(
                    "success",
                    result_json=json.dumps(
                        {"resultUrls": ["https://result.example/clip.mp4"]}
                    ),
                ),
            ]
        )
        url = kie_client.wait_for_task(
            "task_1",
            "secret",
            transport,
            sleeper=lambda _: None,
            randomizer=lambda: 0.0,
            timeout_seconds=60,
        )
        self.assertEqual(url, "https://result.example/clip.mp4")

    def test_fail_state_reports_provider_message(self):
        transport = FakeTransport(
            [task_record("fail", fail_code="CONTENT", fail_msg="input rejected")]
        )
        with self.assertRaisesRegex(
            kie_client.TaskFailedError, "CONTENT.*input rejected"
        ):
            kie_client.wait_for_task("task_1", "secret", transport)

    def test_malformed_result_json_is_schema_error(self):
        transport = FakeTransport([task_record("success", result_json="not-json")])
        with self.assertRaises(kie_client.SchemaError):
            kie_client.wait_for_task("task_1", "secret", transport)

    def test_result_urls_must_be_a_non_empty_list_of_https_urls(self):
        invalid_result_urls = (
            "https://result.example/clip.mp4",
            [],
            [""],
            ["   "],
            ["http://result.example/clip.mp4"],
            ["file:///tmp/clip.mp4"],
            ["/tmp/clip.mp4"],
            ["https:clip.mp4"],
            ["https://result.example/clip.mp4", "file:///tmp/second.mp4"],
            [123],
        )
        for result_urls in invalid_result_urls:
            with self.subTest(result_urls=result_urls):
                with self.assertRaisesRegex(kie_client.SchemaError, "HTTPS"):
                    kie_client.parse_task_record(
                        {
                            "state": "success",
                            "resultJson": json.dumps(
                                {"resultUrls": result_urls}
                            ),
                        }
                    )

    def test_malformed_https_result_urls_are_schema_errors(self):
        malformed_urls = (
            "https://[",
            "https://:443/path",
            "https://user@/path",
            "https://user@example.com/path",
            "https://result.example/a path/clip.mp4",
            "https://result.example/path\nnext",
            "https://result.example/path\x00next",
            "https://result.example:abc/path",
            "https://result.example:-1/path",
            "https://result.example:0/path",
            "https://result.example:70000/path",
            "https://-bad.example/path",
            "https://bad_.example/path",
            "https://999.999.999.999/path",
        )
        for result_url in malformed_urls:
            with self.subTest(result_url=repr(result_url)):
                with self.assertRaisesRegex(kie_client.SchemaError, "HTTPS"):
                    kie_client.parse_task_record(
                        {
                            "state": "success",
                            "resultJson": json.dumps(
                                {"resultUrls": [result_url]}
                            ),
                        }
                    )

    def test_valid_https_result_url_is_returned(self):
        result_url = "https://cdn.example.com:443/video/clip.mp4?token=signed"

        state, parsed_url = kie_client.parse_task_record(
            {
                "state": "success",
                "resultJson": json.dumps({"resultUrls": [result_url]}),
            }
        )

        self.assertEqual(state, "success")
        self.assertEqual(parsed_url, result_url)

    def test_non_object_poll_response_is_schema_error(self):
        transport = FakeTransport([json_response(200, [])])
        with self.assertRaises(kie_client.SchemaError):
            kie_client.wait_for_task("task_1", "secret", transport)

    def test_poll_has_focused_non_retryable_http_messages(self):
        expectations = {
            400: "poll request.*rejected",
            401: "authentication",
            402: "credits",
        }
        for status, message in expectations.items():
            with self.subTest(status=status):
                transport = FakeTransport([json_response(status, {})])
                with self.assertRaisesRegex(kie_client.HttpError, message):
                    kie_client.wait_for_task("task_1", "secret", transport)
                self.assertEqual(len(transport.requests), 1)

    def test_timeout_persists_progress_and_reports_resume_command(self):
        directory = Path(tempfile.mkdtemp())
        manifest_path = directory / "clip.mp4.kie.json"
        output_path = directory / "clip.mp4"
        kie_client.write_manifest_atomic(manifest_path, {"taskId": "task_1"})
        transport = FakeTransport([task_record("generating", progress=45)])

        class Clock:
            now = 0.0

            def __call__(self):
                return self.now

        clock = Clock()

        with self.assertRaisesRegex(
            kie_client.TaskTimeoutError,
            rf"wait --manifest {manifest_path} --output {output_path}",
        ):
            kie_client.wait_for_task(
                "task_1",
                "secret",
                transport,
                sleeper=lambda _: setattr(clock, "now", 60.0),
                timeout_seconds=60,
                manifest_path=manifest_path,
                output_path=output_path,
                monotonic=clock,
            )

        manifest = kie_client.load_manifest(manifest_path)
        self.assertEqual(manifest["state"], "generating")
        self.assertEqual(manifest["progress"], 45)

    def test_poll_request_timeout_uses_remaining_budget_and_checks_after_request(self):
        class Clock:
            now = 100.0

            def __call__(self):
                return self.now

        clock = Clock()

        class SlowTransport(FakeTransport):
            def request(self, *args, **kwargs):
                response = super().request(*args, **kwargs)
                clock.now += 11.0
                return response

        transport = SlowTransport([task_record("waiting")])
        sleeps = []

        with self.assertRaises(kie_client.TaskTimeoutError):
            kie_client.wait_for_task(
                "task_1",
                "secret",
                transport,
                sleeper=sleeps.append,
                timeout_seconds=10,
                monotonic=clock,
            )

        self.assertEqual(transport.requests[0]["timeout"], 10.0)
        self.assertEqual(sleeps, [])

    def test_retry_sleep_is_capped_by_poll_deadline(self):
        class Clock:
            now = 0.0

            def __call__(self):
                return self.now

        clock = Clock()
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock.now += seconds

        transport = FakeTransport([json_response(503, {})])
        with self.assertRaises(kie_client.TaskTimeoutError):
            kie_client.wait_for_task(
                "task_1",
                "secret",
                transport,
                sleeper=sleep,
                randomizer=lambda: 0.0,
                timeout_seconds=2,
                monotonic=clock,
            )

        self.assertEqual(sleeps, [2.0])
        self.assertEqual(len(transport.requests), 1)

    def test_active_poll_sleep_is_capped_by_poll_deadline(self):
        class Clock:
            now = 0.0

            def __call__(self):
                return self.now

        clock = Clock()
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock.now += seconds

        transport = FakeTransport([task_record("waiting")])
        with self.assertRaises(kie_client.TaskTimeoutError):
            kie_client.wait_for_task(
                "task_1",
                "secret",
                transport,
                sleeper=sleep,
                randomizer=lambda: 0.0,
                timeout_seconds=2,
                monotonic=clock,
            )

        self.assertEqual(sleeps, [2.0])
        self.assertEqual(len(transport.requests), 1)

    def test_missing_progress_retains_the_last_manifest_value(self):
        directory = Path(tempfile.mkdtemp())
        manifest_path = directory / "clip.mp4.kie.json"
        kie_client.write_manifest_atomic(manifest_path, {"taskId": "task_1"})
        success_without_progress = json_response(
            200,
            {
                "code": 200,
                "data": {
                    "taskId": "task_1",
                    "state": "success",
                    "resultJson": json.dumps(
                        {"resultUrls": ["https://result.example/clip.mp4"]}
                    ),
                },
            },
        )
        transport = FakeTransport(
            [task_record("generating", progress=45), success_without_progress]
        )

        url = kie_client.wait_for_task(
            "task_1",
            "secret",
            transport,
            sleeper=lambda _: None,
            manifest_path=manifest_path,
        )

        self.assertEqual(url, "https://result.example/clip.mp4")
        self.assertEqual(kie_client.load_manifest(manifest_path)["progress"], 45)


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tempdir.cleanup()

    def assert_main_validation_error(self, argv, api_key="secret-value"):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(
            os.environ, {"KIE_API_KEY": api_key}, clear=True
        ):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = kie_client.main(argv)

        self.assertEqual(status, kie_client.VALIDATION_ERROR_EXIT)
        self.assertEqual(len(stdout.getvalue().strip().splitlines()), 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "error")
        self.assertNotIn(api_key, stdout.getvalue() + stderr.getvalue())
        return payload, stderr.getvalue()

    def test_main_empty_argv_returns_json_validation_error(self):
        payload, stderr = self.assert_main_validation_error([])
        self.assertIn("argument", payload["error"])
        self.assertIn("usage:", stderr)

    def test_main_missing_required_generate_option_returns_json_validation_error(self):
        config = make_config()
        payload, stderr = self.assert_main_validation_error(
            [
                "generate-video",
                "--prompt-file",
                str(config.prompt_file),
                "--start-image",
                str(config.start_image),
            ]
        )
        self.assertIn("argument", payload["error"])
        self.assertIn("--output", stderr)

    def test_main_invalid_integer_and_choice_return_json_validation_errors(self):
        config = make_config()
        cases = (
            (
                [
                    "generate-video",
                    "--prompt-file",
                    str(config.prompt_file),
                    "--start-image",
                    str(config.start_image),
                    "--output",
                    str(config.output),
                    "--duration",
                    "not-an-integer",
                ],
                "invalid int value",
                "secret-value",
            ),
            (["not-a-command"], "invalid choice", "not-a-command"),
        )
        for argv, expected, api_key in cases:
            with self.subTest(argv=argv):
                payload, stderr = self.assert_main_validation_error(argv, api_key)
                self.assertIn("argument", payload["error"])
                self.assertIn(expected, stderr)

    def test_main_help_preserves_normal_exit_zero_behavior(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = kie_client.main(["--help"])

        self.assertEqual(status, 0)
        self.assertIn("generate-video", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_download_uses_part_file_then_replaces_output(self):
        output = Path(self.tempdir.name) / "clip.mp4"
        transport = FakeTransport([binary_response(200, b"fake-mp4")])
        probed = []

        kie_client.download_result(
            "https://result/clip.mp4",
            output,
            transport,
            probe=lambda path: probed.append(path) or {"codec_name": "h264"},
        )

        self.assertEqual(output.read_bytes(), b"fake-mp4")
        self.assertEqual(probed, [Path(str(output) + ".part")])
        self.assertFalse(Path(str(output) + ".part").exists())

    def test_failed_probe_does_not_replace_existing_output(self):
        output = Path(self.tempdir.name) / "clip.mp4"
        output.write_bytes(b"known-good")
        transport = FakeTransport([binary_response(200, b"invalid")])

        def reject(_):
            raise kie_client.MediaValidationError("not a video")

        with self.assertRaisesRegex(kie_client.MediaValidationError, "not a video"):
            kie_client.download_result(
                "https://result/clip.mp4", output, transport, probe=reject
            )

        self.assertEqual(output.read_bytes(), b"known-good")

    def test_probe_video_uses_ffprobe_and_returns_video_stream(self):
        video = Path(self.tempdir.name) / "clip.mp4.part"
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(
                stdout=json.dumps(
                    {"streams": [{"codec_name": "h264", "width": 1280}]}
                )
            )

        stream = kie_client.probe_video(video, runner=runner)

        self.assertEqual(stream["codec_name"], "h264")
        self.assertEqual(calls[0][0][0], "ffprobe")
        self.assertEqual(calls[0][0][-1], str(video))
        self.assertEqual(
            calls[0][1], {"check": True, "capture_output": True, "text": True}
        )

    def test_probe_video_rejects_missing_video_stream(self):
        runner = lambda *_args, **_kwargs: SimpleNamespace(stdout='{"streams": []}')
        with self.assertRaisesRegex(
            kie_client.MediaValidationError, "video stream"
        ):
            kie_client.probe_video(Path("clip.mp4.part"), runner=runner)

    def test_probe_video_wraps_runner_failure(self):
        def runner(*_args, **_kwargs):
            raise subprocess.CalledProcessError(1, ["ffprobe"])

        with self.assertRaisesRegex(kie_client.MediaValidationError, "ffprobe"):
            kie_client.probe_video(Path("clip.mp4.part"), runner=runner)

    def test_wait_resumes_existing_task_without_create_request(self):
        manifest = Path(self.tempdir.name) / "clip.mp4.kie.json"
        output = Path(self.tempdir.name) / "clip.mp4"
        kie_client.write_manifest_atomic(
            manifest,
            {
                "schemaVersion": 1,
                "taskId": "task_existing",
                "state": "generating",
                "output": str(output),
            },
        )
        transport = FakeTransport(
            [
                task_record(
                    "success",
                    result_json=json.dumps(
                        {"resultUrls": ["https://result/clip.mp4"]}
                    ),
                ),
                binary_response(200, b"fake-mp4"),
            ]
        )

        result = kie_client.run_wait(
            manifest,
            output,
            "secret",
            transport,
            probe=lambda _: {"codec_name": "h264"},
            sleeper=lambda _: None,
        )

        self.assertEqual(output.read_bytes(), b"fake-mp4")
        self.assertEqual(result["taskId"], "task_existing")
        self.assertFalse(
            any("createTask" in item["url"] for item in transport.requests)
        )
        finalized = kie_client.load_manifest(manifest)
        self.assertEqual(finalized["resultUrl"], "https://result/clip.mp4")
        self.assertEqual(finalized["outputPath"], str(output))

    def test_media_validation_failure_keeps_result_url_for_resume(self):
        manifest = Path(self.tempdir.name) / "clip.mp4.kie.json"
        output = Path(self.tempdir.name) / "clip.mp4"
        kie_client.write_manifest_atomic(
            manifest,
            {"schemaVersion": 1, "taskId": "task_existing", "state": "generating"},
        )
        transport = FakeTransport(
            [
                task_record(
                    "success",
                    result_json=json.dumps(
                        {"resultUrls": ["https://result/clip.mp4"]}
                    ),
                ),
                binary_response(200, b"invalid"),
            ]
        )

        def reject(_):
            raise kie_client.MediaValidationError("not a video")

        with self.assertRaises(kie_client.MediaValidationError):
            kie_client.run_wait(
                manifest,
                output,
                "secret",
                transport,
                probe=reject,
                sleeper=lambda _: None,
            )

        saved = kie_client.load_manifest(manifest)
        self.assertEqual(saved["state"], "success")
        self.assertEqual(saved["resultUrl"], "https://result/clip.mp4")
        self.assertFalse(output.exists())

    def test_generate_uploads_submits_once_waits_downloads_and_finalizes(self):
        config = replace(make_config(), output=Path(self.tempdir.name) / "clip.mp4")
        transport = FakeTransport(
            [
                json_response(
                    200,
                    {
                        "success": True,
                        "code": 200,
                        "data": {"downloadUrl": "https://files/start.png"},
                    },
                ),
                json_response(
                    200, {"code": 200, "data": {"taskId": "task_created"}}
                ),
                task_record(
                    "success",
                    result_json=json.dumps(
                        {"resultUrls": ["https://result/clip.mp4"]}
                    ),
                ),
                binary_response(200, b"fake-mp4"),
            ]
        )

        result = kie_client.run_generate(
            config,
            "secret",
            transport,
            probe=lambda _: {"codec_name": "h264"},
            sleeper=lambda _: None,
        )

        self.assertEqual(result["taskId"], "task_created")
        self.assertEqual(config.output.read_bytes(), b"fake-mp4")
        create_requests = [
            request
            for request in transport.requests
            if "createTask" in request["url"]
        ]
        self.assertEqual(len(create_requests), 1)
        manifest = kie_client.load_manifest(kie_client.manifest_path_for(config.output))
        self.assertEqual(manifest["state"], "success")
        self.assertEqual(manifest["resultUrl"], "https://result/clip.mp4")

    def test_error_text_redacts_api_key(self):
        text = kie_client.redact(
            "Authorization: Bearer secret-value", "secret-value"
        )
        self.assertNotIn("secret-value", text)

    def test_main_writes_one_json_result_and_progress_to_stderr(self):
        config = make_config()
        stdout = io.StringIO()
        stderr = io.StringIO()
        result = {
            "status": "success",
            "taskId": "task_1",
            "output": str(config.output),
        }
        argv = [
            "generate-video",
            "--prompt-file",
            str(config.prompt_file),
            "--start-image",
            str(config.start_image),
            "--output",
            str(config.output),
        ]

        with mock.patch.dict(os.environ, {"KIE_API_KEY": "secret"}, clear=True):
            with mock.patch.object(kie_client, "run_generate", return_value=result):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    status = kie_client.main(argv)

        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue()), result)
        self.assertEqual(len(stdout.getvalue().strip().splitlines()), 1)
        self.assertIn("Generating", stderr.getvalue())
        self.assertNotIn("secret", stderr.getvalue())

    def test_main_returns_distinct_status_and_redacts_expected_client_error(self):
        config = make_config()
        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "generate-video",
            "--prompt-file",
            str(config.prompt_file),
            "--start-image",
            str(config.start_image),
            "--output",
            str(config.output),
        ]

        with mock.patch.dict(os.environ, {"KIE_API_KEY": "secret-value"}, clear=True):
            with mock.patch.object(
                kie_client,
                "run_generate",
                side_effect=kie_client.HttpError("failed with secret-value"),
            ):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    status = kie_client.main(argv)

        self.assertEqual(status, kie_client.CLIENT_ERROR_EXIT)
        self.assertNotEqual(status, 0)
        self.assertNotIn("secret-value", stdout.getvalue() + stderr.getvalue())
        self.assertEqual(json.loads(stdout.getvalue())["status"], "error")

    def test_main_reports_local_download_io_failure_as_client_error(self):
        config = make_config()
        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "generate-video",
            "--prompt-file",
            str(config.prompt_file),
            "--start-image",
            str(config.start_image),
            "--output",
            str(config.output),
        ]

        with mock.patch.dict(os.environ, {"KIE_API_KEY": "secret"}, clear=True):
            with mock.patch.object(
                kie_client, "run_generate", side_effect=OSError("disk full")
            ):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    status = kie_client.main(argv)

        self.assertEqual(status, kie_client.CLIENT_ERROR_EXIT)
        self.assertEqual(json.loads(stdout.getvalue())["error"], "disk full")

    def test_invalid_result_url_is_reported_as_clean_json_cli_error(self):
        output = Path(self.tempdir.name) / "clip.mp4"
        manifest = kie_client.manifest_path_for(output)
        kie_client.write_manifest_atomic(
            manifest,
            {"schemaVersion": 1, "taskId": "task_1", "state": "generating"},
        )
        transport = FakeTransport(
            [
                task_record(
                    "success",
                    result_json=json.dumps({"resultUrls": ["https://["]}),
                )
            ]
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with mock.patch.dict(os.environ, {"KIE_API_KEY": "secret"}, clear=True):
            with mock.patch.object(kie_client, "UrllibTransport", return_value=transport):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    status = kie_client.main(
                        [
                            "wait",
                            "--manifest",
                            str(manifest),
                            "--output",
                            str(output),
                        ]
                    )

        self.assertEqual(status, kie_client.CLIENT_ERROR_EXIT)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "error")
        self.assertIn("HTTPS", stdout.getvalue())
        self.assertNotIn("Traceback", stdout.getvalue() + stderr.getvalue())


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

        mobile = pipeline.split("## 6. Native 9:16 mobile chain", 1)[1]
        extraction_loop = """for name in $NAMES; do
  ffmpeg -v error -y -ss 0 -i "$WORK/dive-mobile_$name.mp4" \\
    -frames:v 1 -q:v 2 "$WORK/first-mobile_$name.png"
  ffmpeg -v error -y -sseof -0.15 -i "$WORK/dive-mobile_$name.mp4" \\
    -frames:v 1 -q:v 2 "$WORK/last-mobile_$name.png"
done"""
        self.assertIn(extraction_loop, mobile)
        connector_inputs = """--start-image "$WORK/last-mobile_$previous.png" \\
  --end-image "$WORK/first-mobile_$next.png"""
        self.assertIn(connector_inputs, mobile)
        self.assertLess(mobile.index(extraction_loop), mobile.index(connector_inputs))
        self.assertIn(
            'name=farm\npython3 "$SKILL/scripts/kie_client.py" generate-video',
            mobile,
        )


# Keep this entry point at the end of the test file as later test classes are added.
if __name__ == "__main__":
    unittest.main()
