from __future__ import annotations

import json
import logging
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from wander.logging_config import configure_logging, event_fields, shutdown_logging


class PersistentLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_excepthook = sys.excepthook
        self.original_thread_excepthook = threading.excepthook

    def tearDown(self) -> None:
        logger = logging.getLogger("wander")
        for handler in logger.handlers:
            handler.close()
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        sys.excepthook = self.original_excepthook
        threading.excepthook = self.original_thread_excepthook

    def test_structured_event_contains_runtime_and_operational_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            runtime = configure_logging(
                temporary_directory,
                "test-version",
                run_id="test-run",
                stderr=False,
            )
            logging.getLogger("wander.test").info(
                "Ledger opened",
                extra=event_fields(
                    "ledger.test.opened",
                    ledger_id="ledger-1",
                    schema_version=5,
                ),
            )

            document = json.loads(Path(runtime.path).read_text(encoding="utf-8"))

            self.assertEqual("ledger.test.opened", document["event"])
            self.assertEqual("test-run", document["run_id"])
            self.assertEqual("test-version", document["app_version"])
            self.assertEqual("ledger-1", document["ledger_id"])
            self.assertEqual(5, document["schema_version"])
            self.assertNotIn("authorization", document)
            shutdown_logging()

    def test_log_file_rotates_at_the_configured_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            runtime = configure_logging(
                temporary_directory,
                "test-version",
                run_id="rotation-run",
                max_bytes=300,
                backup_count=2,
                stderr=False,
            )
            logger = logging.getLogger("wander.test")
            for index in range(10):
                logger.info(
                    "Rotating event",
                    extra=event_fields("rotation.event", sequence=index),
                )

            self.assertTrue(Path(runtime.path).is_file())
            self.assertTrue(Path(f"{runtime.path}.1").is_file())
            shutdown_logging()


if __name__ == "__main__":
    unittest.main()
