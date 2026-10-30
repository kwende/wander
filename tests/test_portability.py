from __future__ import annotations

import json
import unittest
from pathlib import Path


class PortabilityTests(unittest.TestCase):
    def test_plugin_mcp_entry_point_is_checkout_relative(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        configuration = json.loads((repository / "mcp.json").read_text(encoding="utf-8"))
        server = configuration["mcpServers"]["wander"]

        self.assertEqual("${PLUGIN_ROOT}", server["cwd"])
        self.assertIn("scripts/start-mcp.ps1", server["args"])
        self.assertNotIn(str(repository), json.dumps(configuration))


if __name__ == "__main__":
    unittest.main()
