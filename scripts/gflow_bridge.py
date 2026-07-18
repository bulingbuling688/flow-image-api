from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_gflow_args(
    *, prompt: str, model: str, aspect_ratio: str, output_dir: Path, profile: str
) -> list[str]:
    return [
        "image",
        "t2i",
        prompt,
        "--model",
        model,
        "--aspect",
        aspect_ratio,
        "-n",
        "1",
        "--out",
        str(output_dir),
        "--profile",
        profile,
        "--json",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Private stdin bridge for gflow")
    parser.add_argument("--model", required=True)
    parser.add_argument("--aspect", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    arguments = parser.parse_args()
    prompt = " ".join(sys.stdin.read(4097).split())
    if not prompt or len(prompt) > 4000:
        parser.error("stdin prompt must contain between 1 and 4000 characters")

    from gflow_cli.cli import main as gflow_main  # pyright: ignore[reportMissingImports]

    gflow_main.main(
        args=build_gflow_args(
            prompt=prompt,
            model=arguments.model,
            aspect_ratio=arguments.aspect,
            output_dir=arguments.out,
            profile=arguments.profile,
        ),
        prog_name="gflow",
        standalone_mode=True,
    )


if __name__ == "__main__":
    main()
