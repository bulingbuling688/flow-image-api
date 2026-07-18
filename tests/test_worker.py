from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flow_image_api.worker import FlowWorker, WorkerSettings


class WorkerGenerationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = WorkerSettings(
            api_base_url="https://example.test",
            worker_token="worker-token",
            worker_id="worker-a",
            gflow_runner_ps1=self.root / "gflow-run.ps1",
            gflow_profile="profile-a",
            output_dir=self.root / "outputs",
        )
        self.worker = FlowWorker(self.settings)

    def tearDown(self) -> None:
        self.worker.close()
        self.temporary.cleanup()

    @patch("flow_image_api.worker.subprocess.run")
    def test_generated_file_is_cached_before_upload(self, run_mock) -> None:
        job = {
            "id": "job-1",
            "prompt": "one line only",
            "model": "nano2",
            "aspect_ratio": "16:9",
        }
        job_dir = self.settings.output_dir / "job-1"
        job_dir.mkdir(parents=True)
        image_path = job_dir / "result.jpg"
        image_path.write_bytes(b"\xff\xd8\xfftest")
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"images": [{"local_path": str(image_path)}]}),
            stderr="",
        )

        generated_path, mime_type = self.worker._generate(job)
        self.assertEqual(generated_path, image_path)
        self.assertEqual(mime_type, "image/jpeg")
        self.assertTrue((job_dir / "generated.json").is_file())

        run_mock.reset_mock()
        cached_path, cached_mime = self.worker._generate(job)
        self.assertEqual(cached_path, image_path)
        self.assertEqual(cached_mime, "image/jpeg")
        run_mock.assert_not_called()

    @patch("flow_image_api.worker.subprocess.run")
    def test_prompt_is_sent_through_stdin(self, run_mock) -> None:
        job = {
            "id": "job-2",
            "prompt": "private prompt",
            "model": "nano2",
            "aspect_ratio": "1:1",
        }
        job_dir = self.settings.output_dir / "job-2"
        job_dir.mkdir(parents=True)
        image_path = job_dir / "result.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nrest")
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"images": [{"local_path": str(image_path)}]}),
            stderr="",
        )

        self.worker._generate(job)

        command = run_mock.call_args.args[0]
        self.assertNotIn("private prompt", command)
        self.assertEqual(run_mock.call_args.kwargs["input"], "private prompt\n")


if __name__ == "__main__":
    unittest.main()
