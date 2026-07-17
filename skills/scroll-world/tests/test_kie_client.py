import os
import json
import sys
import tempfile
import unittest
from dataclasses import replace
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
        return self.responses.pop(0)


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

    def test_create_task_does_not_retry_a_retryable_failure(self):
        transport = FakeTransport([json_response(503, {"msg": "try later"})])
        with self.assertRaisesRegex(kie_client.HttpError, "not retried"):
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
        self.assertEqual(manifest["createdAt"], manifest["updatedAt"])
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

    def test_non_object_poll_response_is_schema_error(self):
        transport = FakeTransport([json_response(200, [])])
        with self.assertRaises(kie_client.SchemaError):
            kie_client.wait_for_task("task_1", "secret", transport)

    def test_timeout_persists_progress_and_reports_resume_command(self):
        directory = Path(tempfile.mkdtemp())
        manifest_path = directory / "clip.mp4.kie.json"
        output_path = directory / "clip.mp4"
        kie_client.write_manifest_atomic(manifest_path, {"taskId": "task_1"})
        transport = FakeTransport([task_record("generating", progress=45)])
        times = iter((0.0, 0.0, 60.0))

        with self.assertRaisesRegex(
            kie_client.TaskTimeoutError,
            rf"wait --manifest {manifest_path} --output {output_path}",
        ):
            kie_client.wait_for_task(
                "task_1",
                "secret",
                transport,
                sleeper=lambda _: None,
                timeout_seconds=60,
                manifest_path=manifest_path,
                output_path=output_path,
                monotonic=lambda: next(times),
            )

        manifest = kie_client.load_manifest(manifest_path)
        self.assertEqual(manifest["state"], "generating")
        self.assertEqual(manifest["progress"], 45)


# Keep this entry point at the end of the test file as later test classes are added.
if __name__ == "__main__":
    unittest.main()
