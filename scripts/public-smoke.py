from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit and download one API image job")
    parser.add_argument("prompt")
    parser.add_argument("--base-url", default=os.environ.get("FLOW_API_BASE_URL"))
    parser.add_argument("--api-key", default=os.environ.get("FLOW_API_KEY"))
    parser.add_argument("--out", type=Path, default=Path("flow-image-api-result.jpg"))
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    if not args.base_url or not args.api_key:
        parser.error("Set FLOW_API_BASE_URL and FLOW_API_KEY or pass both options")

    headers = {"Authorization": f"Bearer {args.api_key}"}
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=60) as client:
        response = client.post(
            "/v1/images/generations",
            json={"prompt": args.prompt, "model": "nano2", "aspect_ratio": "16:9"},
        )
        response.raise_for_status()
        job = response.json()
        deadline = time.monotonic() + args.timeout
        while job["status"] in {"queued", "processing"}:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Job {job['id']} did not finish in time")
            time.sleep(3)
            job = client.get(f"/v1/jobs/{job['id']}").raise_for_status().json()
        if job["status"] != "succeeded":
            raise RuntimeError(f"Job failed: {job.get('error', {})}")
        image = client.get(f"/v1/images/{job['id']}")
        image.raise_for_status()
        args.out.write_bytes(image.content)
        print(f"job={job['id']} output={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
