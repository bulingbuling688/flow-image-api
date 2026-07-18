from __future__ import annotations

import unittest
from pathlib import Path

from scripts.gflow_bridge import build_gflow_args


class GflowBridgeTestCase(unittest.TestCase):
    def test_bridge_builds_single_prompt_json_command(self) -> None:
        args = build_gflow_args(
            prompt="private prompt",
            model="nano2",
            aspect_ratio="16:9",
            output_dir=Path("output"),
            profile="profile-a",
        )
        self.assertEqual(args[:3], ["image", "t2i", "private prompt"])
        self.assertIn("--json", args)
        self.assertNotIn("--stdin", args)


if __name__ == "__main__":
    unittest.main()
