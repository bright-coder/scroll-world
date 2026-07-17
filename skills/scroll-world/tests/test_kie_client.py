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
