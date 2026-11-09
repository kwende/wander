from __future__ import annotations

import json
import unittest
from pathlib import Path


class PortabilityTests(unittest.TestCase):
    def test_repository_and_plugin_mcp_entries_use_desktop_http_transport(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        for configuration_path in (repository / ".mcp.json", repository / "mcp.json"):
            configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
            server = configuration["mcpServers"]["wander"]

            self.assertEqual("http", server["type"])
            self.assertEqual("http://127.0.0.1:8766/mcp", server["url"])
            self.assertNotIn("command", server)
            self.assertNotIn("args", server)

    def test_codex_installer_registers_the_desktop_http_endpoint(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        installer = (repository / "scripts" / "install-codex.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("mcp add wander --url $mcpUrl", installer)
        self.assertIn("The MCP endpoint is available only while Wander is open.", installer)


if __name__ == "__main__":
    unittest.main()
