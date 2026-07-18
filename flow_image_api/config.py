from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from pathlib import Path


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    database_path: Path
    image_dir: Path
    api_key_sha256: str
    worker_token_sha256: str
    public_base_url: str
    lease_seconds: int = 900
    worker_online_seconds: int = 45
    max_pending_jobs: int = 100
    max_image_bytes: int = 20 * 1024 * 1024

    def __post_init__(self) -> None:
        for name, value in (
            ("api_key_sha256", self.api_key_sha256),
            ("worker_token_sha256", self.worker_token_sha256),
        ):
            if not _SHA256_RE.fullmatch(value):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if self.lease_seconds < 30:
            raise ValueError("lease_seconds must be at least 30")
        if self.worker_online_seconds < 5:
            raise ValueError("worker_online_seconds must be at least 5")
        if self.max_pending_jobs < 1:
            raise ValueError("max_pending_jobs must be positive")
        if self.max_image_bytes < 1024:
            raise ValueError("max_image_bytes must be at least 1024")

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_path=Path(_required_env("FLOW_DATABASE_PATH")),
            image_dir=Path(_required_env("FLOW_IMAGE_DIR")),
            api_key_sha256=_required_env("FLOW_API_KEY_SHA256"),
            worker_token_sha256=_required_env("FLOW_WORKER_TOKEN_SHA256"),
            public_base_url=_required_env("FLOW_PUBLIC_BASE_URL").rstrip("/"),
            lease_seconds=int(os.environ.get("FLOW_LEASE_SECONDS", "900")),
            worker_online_seconds=int(os.environ.get("FLOW_WORKER_ONLINE_SECONDS", "45")),
            max_pending_jobs=int(os.environ.get("FLOW_MAX_PENDING_JOBS", "100")),
            max_image_bytes=int(os.environ.get("FLOW_MAX_IMAGE_BYTES", str(20 * 1024 * 1024))),
        )

    def valid_api_key(self, token: str) -> bool:
        return hmac.compare_digest(token_digest(token), self.api_key_sha256)

    def valid_worker_token(self, token: str) -> bool:
        return hmac.compare_digest(token_digest(token), self.worker_token_sha256)
