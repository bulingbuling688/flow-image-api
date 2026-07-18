from __future__ import annotations

import base64
import binascii
import os
import re
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings
from .database import Database, JobStateError, QueueFullError


ModelName = Literal["nano2", "nano-pro", "image4"]
AspectRatio = Literal["9:16", "16:9", "1:1", "4:3", "3:4"]
MimeType = Literal["image/jpeg", "image/png", "image/webp"]
_ERROR_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class GenerationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    model: ModelName = "nano2"
    aspect_ratio: AspectRatio = "16:9"
    n: Literal[1] = 1


class WorkerIdentity(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)


class CompleteJobRequest(WorkerIdentity):
    mime_type: MimeType
    image_base64: str = Field(min_length=4)


class FailJobRequest(WorkerIdentity):
    error_code: str = Field(min_length=1, max_length=64)


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    return token.strip()


def _image_extension(data: bytes, mime_type: str) -> str:
    signatures = {
        "image/jpeg": (b"\xff\xd8\xff", ".jpg"),
        "image/png": (b"\x89PNG\r\n\x1a\n", ".png"),
        "image/webp": (b"RIFF", ".webp"),
    }
    signature, extension = signatures[mime_type]
    if not data.startswith(signature):
        raise HTTPException(status_code=422, detail="Image bytes do not match MIME type")
    if mime_type == "image/webp" and data[8:12] != b"WEBP":
        raise HTTPException(status_code=422, detail="Invalid WebP image")
    return extension


def _job_payload(job: dict[str, object], settings: Settings) -> dict[str, object]:
    job_id = str(job["id"])
    payload: dict[str, object] = {
        "id": job_id,
        "object": "image_generation.job",
        "status": job["status"],
        "model": job["model"],
        "aspect_ratio": job["aspect_ratio"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "status_url": f"{settings.public_base_url}/v1/jobs/{job_id}",
    }
    if job["status"] == "succeeded":
        payload["result_url"] = f"{settings.public_base_url}/v1/images/{job_id}"
    if job["status"] == "failed":
        payload["error"] = {"code": job["error_code"] or "generation_failed"}
    return payload


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    database = Database(resolved.database_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        resolved.image_dir.mkdir(parents=True, exist_ok=True)
        database.initialize()
        yield

    app = FastAPI(
        title="Flow Image API",
        version=__version__,
        description="Authenticated queue API backed by a Windows Google Flow worker.",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.database = database

    def require_api_key(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        if not resolved.valid_api_key(_bearer_token(authorization)):
            raise HTTPException(status_code=401, detail="Invalid bearer token")

    def require_worker_token(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        if not resolved.valid_worker_token(_bearer_token(authorization)):
            raise HTTPException(status_code=401, detail="Invalid worker token")

    @app.get("/")
    def index() -> dict[str, str]:
        return {
            "name": "flow-image-api",
            "version": __version__,
            "health": "/health",
            "docs": "/docs",
        }

    @app.get("/health")
    def health() -> dict[str, object]:
        database.ping()
        worker = database.worker_state()
        online = bool(
            worker and time.time() - float(worker["last_seen_at"]) <= resolved.worker_online_seconds
        )
        return {
            "status": "ok",
            "queue": "ready",
            "worker_online": online,
            "version": __version__,
        }

    @app.post(
        "/v1/images/generations",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_api_key)],
    )
    def create_generation(body: GenerationRequest) -> dict[str, object]:
        prompt = " ".join(body.prompt.split())
        if not prompt:
            raise HTTPException(status_code=422, detail="Prompt cannot be blank")
        try:
            job = database.create_job(
                prompt=prompt,
                model=body.model,
                aspect_ratio=body.aspect_ratio,
                max_pending_jobs=resolved.max_pending_jobs,
            )
        except QueueFullError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return _job_payload(job, resolved)

    @app.get("/v1/jobs/{job_id}", dependencies=[Depends(require_api_key)])
    def get_job(job_id: str) -> dict[str, object]:
        try:
            job = database.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        return _job_payload(job, resolved)

    @app.get(
        "/v1/images/{job_id}",
        response_class=FileResponse,
        dependencies=[Depends(require_api_key)],
    )
    def get_image(job_id: str) -> FileResponse:
        try:
            job = database.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        if job["status"] != "succeeded" or not job["file_name"]:
            raise HTTPException(status_code=409, detail="Image is not ready")
        path = resolved.image_dir / str(job["file_name"])
        if not path.is_file():
            raise HTTPException(status_code=410, detail="Image file is unavailable")
        return FileResponse(
            path,
            media_type=str(job["mime_type"]),
            filename=path.name,
        )

    @app.post(
        "/internal/worker/heartbeat",
        dependencies=[Depends(require_worker_token)],
    )
    def worker_heartbeat(body: WorkerIdentity) -> dict[str, object]:
        seen_at = database.heartbeat(body.worker_id)
        return {"status": "ok", "seen_at": seen_at}

    @app.post(
        "/internal/jobs/lease",
        dependencies=[Depends(require_worker_token)],
    )
    def lease_job(body: WorkerIdentity, response: Response) -> dict[str, object] | None:
        database.heartbeat(body.worker_id)
        job = database.lease_next(worker_id=body.worker_id, lease_seconds=resolved.lease_seconds)
        if job is None:
            response.status_code = status.HTTP_204_NO_CONTENT
            return None
        return {
            "id": job["id"],
            "prompt": job["prompt"],
            "model": job["model"],
            "aspect_ratio": job["aspect_ratio"],
            "lease_expires_at": job["lease_expires_at"],
        }

    @app.post(
        "/internal/jobs/{job_id}/complete",
        dependencies=[Depends(require_worker_token)],
    )
    def complete_job(job_id: str, body: CompleteJobRequest) -> dict[str, object]:
        try:
            image_bytes = base64.b64decode(body.image_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid base64 image") from exc
        if len(image_bytes) > resolved.max_image_bytes:
            raise HTTPException(status_code=413, detail="Image exceeds size limit")
        extension = _image_extension(image_bytes, body.mime_type)
        file_name = f"{job_id}{extension}"
        target = resolved.image_dir / file_name
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{job_id}-", dir=resolved.image_dir)
        try:
            with os.fdopen(descriptor, "wb") as temporary_file:
                temporary_file.write(image_bytes)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_name, target)
            try:
                job = database.complete_job(
                    job_id=job_id,
                    worker_id=body.worker_id,
                    mime_type=body.mime_type,
                    file_name=file_name,
                )
            except (KeyError, JobStateError) as exc:
                target.unlink(missing_ok=True)
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        return _job_payload(job, resolved)

    @app.post(
        "/internal/jobs/{job_id}/fail",
        dependencies=[Depends(require_worker_token)],
    )
    def fail_job(job_id: str, body: FailJobRequest) -> dict[str, object]:
        if not _ERROR_CODE_RE.fullmatch(body.error_code):
            raise HTTPException(status_code=422, detail="Invalid error code")
        try:
            job = database.fail_job(
                job_id=job_id,
                worker_id=body.worker_id,
                error_code=body.error_code,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except JobStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _job_payload(job, resolved)

    return app
