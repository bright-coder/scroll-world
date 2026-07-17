import os
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


# Keep this entry point at the end of the test file as later test classes are added.
if __name__ == "__main__":
    unittest.main()
