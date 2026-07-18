from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
            raise ValueError(f"Invalid environment line {number} in {path}")
        values[key.strip()] = value.strip()
    return values


def _required(values: dict[str, str], name: str) -> str:
    value = values.get(name, os.environ.get(name, "")).strip()
    if not value:
        raise RuntimeError(f"Missing worker setting: {name}")
    return value


def _system_proxy() -> str | None:
    proxies = urllib.request.getproxies()
    proxy = proxies.get("https") or proxies.get("http")
    if not proxy:
        return None
    if "://" not in proxy:
        return f"http://{proxy}"
    return proxy


@dataclass(frozen=True)
class WorkerSettings:
    api_base_url: str
    worker_token: str
    worker_id: str
    gflow_runner_ps1: Path
    gflow_profile: str
    output_dir: Path
    http_proxy: str | None = None
    poll_seconds: float = 3.0
    generation_timeout_seconds: int = 900
    keep_local_outputs: bool = True

    @classmethod
    def from_file(cls, path: Path) -> WorkerSettings:
        values = _load_env_file(path)
        return cls(
            api_base_url=_required(values, "FLOW_API_BASE_URL").rstrip("/"),
            worker_token=_required(values, "FLOW_WORKER_TOKEN"),
            worker_id=values.get("FLOW_WORKER_ID", socket.gethostname()).strip(),
            gflow_runner_ps1=Path(_required(values, "FLOW_GFLOW_RUNNER_PS1")),
            gflow_profile=_required(values, "FLOW_GFLOW_PROFILE"),
            output_dir=Path(_required(values, "FLOW_GFLOW_OUTPUT_DIR")),
            http_proxy=values.get("FLOW_HTTP_PROXY") or _system_proxy(),
            poll_seconds=float(values.get("FLOW_WORKER_POLL_SECONDS", "3")),
            generation_timeout_seconds=int(values.get("FLOW_GENERATION_TIMEOUT_SECONDS", "900")),
            keep_local_outputs=values.get("FLOW_KEEP_LOCAL_OUTPUTS", "true").lower()
            in {"1", "true", "yes", "on"},
        )


class GenerationFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _mime_type(path: Path) -> str:
    with path.open("rb") as image_file:
        prefix = image_file.read(12)
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return "image/webp"
    raise GenerationFailure("unsupported_image_format")


class FlowWorker:
    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        self.client = httpx.Client(
            base_url=settings.api_base_url,
            headers={"Authorization": f"Bearer {settings.worker_token}"},
            timeout=httpx.Timeout(30, connect=30),
            follow_redirects=False,
            proxy=settings.http_proxy,
            trust_env=settings.http_proxy is None,
        )

    def close(self) -> None:
        self.client.close()

    def heartbeat(self) -> None:
        response = self.client.post(
            "/internal/worker/heartbeat",
            json={"worker_id": self.settings.worker_id},
        )
        response.raise_for_status()

    def lease(self) -> dict[str, Any] | None:
        response = self.client.post(
            "/internal/jobs/lease",
            json={"worker_id": self.settings.worker_id},
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return response.json()

    def _generate(self, job: dict[str, Any]) -> tuple[Path, str]:
        job_dir = self.settings.output_dir / str(job["id"])
        job_dir.mkdir(parents=True, exist_ok=True)
        marker = job_dir / "generated.json"
        if marker.is_file():
            try:
                cached = json.loads(marker.read_text(encoding="utf-8"))
                cached_name = Path(cached["file_name"]).name
                cached_path = job_dir / cached_name
                cached_mime = str(cached["mime_type"])
                if cached_path.is_file() and cached_mime == _mime_type(cached_path):
                    return cached_path, cached_mime
            except (json.JSONDecodeError, KeyError, OSError, GenerationFailure):
                marker.unlink(missing_ok=True)
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.settings.gflow_runner_ps1),
            "image",
            "t2i",
            "--stdin",
            "--model",
            str(job["model"]),
            "--aspect",
            str(job["aspect_ratio"]),
            "-n",
            "1",
            "--out",
            str(job_dir),
            "--profile",
            self.settings.gflow_profile,
        ]
        try:
            completed = subprocess.run(
                command,
                input=f"{job['prompt']}\n",
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.settings.generation_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GenerationFailure("gflow_timeout") from exc
        if completed.returncode != 0:
            raise GenerationFailure(f"gflow_exit_{completed.returncode}")
        candidates = sorted(
            (
                path
                for path in job_dir.iterdir()
                if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
            ),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        if not candidates:
            raise GenerationFailure("gflow_output_missing")
        image_path = candidates[0]
        if image_path.parent.resolve() != job_dir.resolve():
            local_image = job_dir / image_path.name
            shutil.copy2(image_path, local_image)
            image_path = local_image
        mime_type = _mime_type(image_path)
        marker.write_text(
            json.dumps({"file_name": image_path.name, "mime_type": mime_type}),
            encoding="utf-8",
        )
        return image_path, mime_type

    def _complete(self, job: dict[str, Any], image_path: Path, mime_type: str) -> None:
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        response = self.client.post(
            f"/internal/jobs/{job['id']}/complete",
            json={
                "worker_id": self.settings.worker_id,
                "mime_type": mime_type,
                "image_base64": encoded,
            },
            timeout=httpx.Timeout(120, connect=30),
        )
        response.raise_for_status()

    def _fail(self, job: dict[str, Any], code: str) -> None:
        response = self.client.post(
            f"/internal/jobs/{job['id']}/fail",
            json={"worker_id": self.settings.worker_id, "error_code": code},
        )
        response.raise_for_status()

    def run_once(self) -> bool:
        job = self.lease()
        if job is None:
            return False
        job_id = str(job["id"])
        print(f"job_started id={job_id}", flush=True)
        try:
            image_path, mime_type = self._generate(job)
            self._complete(job, image_path, mime_type)
            print(f"job_succeeded id={job_id}", flush=True)
            if not self.settings.keep_local_outputs:
                shutil.rmtree(image_path.parent, ignore_errors=True)
        except GenerationFailure as exc:
            self._fail(job, exc.code)
            print(f"job_failed id={job_id} code={exc.code}", flush=True)
        return True

    def run_forever(self) -> None:
        print(
            f"worker_started id={self.settings.worker_id} api={self.settings.api_base_url}",
            flush=True,
        )
        while True:
            try:
                processed = self.run_once()
                if not processed:
                    time.sleep(self.settings.poll_seconds)
            except (httpx.HTTPError, OSError) as exc:
                print(f"worker_connection_error type={type(exc).__name__}", flush=True)
                time.sleep(max(self.settings.poll_seconds, 5))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Poll and execute Flow image jobs")
    parser.add_argument(
        "--env",
        type=Path,
        required=True,
        help="Path to the private worker environment file",
    )
    parser.add_argument("--once", action="store_true", help="Process at most one job")
    arguments = parser.parse_args(argv)
    worker = FlowWorker(WorkerSettings.from_file(arguments.env))
    try:
        if arguments.once:
            worker.heartbeat()
            worker.run_once()
        else:
            worker.run_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        worker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
