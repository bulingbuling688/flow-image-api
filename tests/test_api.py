from __future__ import annotations

import base64
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from flow_image_api.config import Settings, token_digest
from flow_image_api.server import create_app


class ApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.api_key = "api-test-key"
        self.worker_token = "worker-test-token"
        self.settings = Settings(
            database_path=root / "jobs.db",
            image_dir=root / "images",
            api_key_sha256=token_digest(self.api_key),
            worker_token_sha256=token_digest(self.worker_token),
            public_base_url="https://example.test",
            lease_seconds=30,
            worker_online_seconds=30,
            max_pending_jobs=2,
            max_image_bytes=1024 * 1024,
        )
        self.client_context = TestClient(create_app(self.settings))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temporary.cleanup()

    @property
    def api_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    @property
    def worker_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.worker_token}"}

    def create_job(self) -> dict[str, object]:
        response = self.client.post(
            "/v1/images/generations",
            headers=self.api_headers,
            json={
                "prompt": "A red apple on a white table",
                "model": "nano2",
                "aspect_ratio": "16:9",
            },
        )
        self.assertEqual(response.status_code, 202)
        return response.json()

    def lease_job(self, worker_id: str = "worker-a") -> dict[str, object]:
        response = self.client.post(
            "/internal/jobs/lease",
            headers=self.worker_headers,
            json={"worker_id": worker_id},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_health_reports_worker_liveness(self) -> None:
        self.assertEqual(self.client.get("/health").json()["worker_online"], False)
        response = self.client.post(
            "/internal/worker/heartbeat",
            headers=self.worker_headers,
            json={"worker_id": "worker-a"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/health").json()["worker_online"], True)

    def test_api_key_is_required(self) -> None:
        missing = self.client.post("/v1/images/generations", json={"prompt": "test"})
        wrong = self.client.post(
            "/v1/images/generations",
            headers={"Authorization": "Bearer wrong"},
            json={"prompt": "test"},
        )
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)

    def test_full_job_lifecycle(self) -> None:
        created = self.create_job()
        self.assertEqual(created["status"], "queued")
        self.assertNotIn("prompt", created)

        leased = self.lease_job()
        self.assertEqual(leased["id"], created["id"])
        self.assertEqual(leased["prompt"], "A red apple on a white table")

        fake_jpeg = b"\xff\xd8\xff" + b"test-image-content"
        completed = self.client.post(
            f"/internal/jobs/{created['id']}/complete",
            headers=self.worker_headers,
            json={
                "worker_id": "worker-a",
                "mime_type": "image/jpeg",
                "image_base64": base64.b64encode(fake_jpeg).decode("ascii"),
            },
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "succeeded")

        status_response = self.client.get(f"/v1/jobs/{created['id']}", headers=self.api_headers)
        self.assertEqual(status_response.json()["status"], "succeeded")
        image_response = self.client.get(f"/v1/images/{created['id']}", headers=self.api_headers)
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response.content, fake_jpeg)
        self.assertEqual(image_response.headers["content-type"], "image/jpeg")

    def test_multiline_prompt_is_one_generation(self) -> None:
        response = self.client.post(
            "/v1/images/generations",
            headers=self.api_headers,
            json={"prompt": "first line\nsecond line"},
        )
        self.assertEqual(response.status_code, 202)
        leased = self.lease_job()
        self.assertEqual(leased["prompt"], "first line second line")

    def test_worker_cannot_complete_an_old_lease(self) -> None:
        job = self.create_job()
        self.lease_job("worker-a")
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("UPDATE jobs SET lease_expires_at = 0 WHERE id = ?", (job["id"],))
            connection.commit()
        self.lease_job("worker-b")
        response = self.client.post(
            f"/internal/jobs/{job['id']}/fail",
            headers=self.worker_headers,
            json={"worker_id": "worker-a", "error_code": "stale_worker"},
        )
        self.assertEqual(response.status_code, 409)

    def test_queue_limit_is_enforced(self) -> None:
        self.create_job()
        self.create_job()
        response = self.client.post(
            "/v1/images/generations",
            headers=self.api_headers,
            json={"prompt": "third job"},
        )
        self.assertEqual(response.status_code, 429)

    def test_image_signature_must_match_mime_type(self) -> None:
        job = self.create_job()
        self.lease_job()
        response = self.client.post(
            f"/internal/jobs/{job['id']}/complete",
            headers=self.worker_headers,
            json={
                "worker_id": "worker-a",
                "mime_type": "image/png",
                "image_base64": base64.b64encode(b"not-a-png").decode("ascii"),
            },
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
