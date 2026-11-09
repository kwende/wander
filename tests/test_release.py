from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wander.release import (
    MANIFEST_NAME,
    ReleaseManifestError,
    create_build_manifest,
    verify_build_manifest,
)


class ReleaseManifestTests(unittest.TestCase):
    def test_manifest_records_identity_and_verifies_exact_file_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            bundle = Path(temporary_directory)
            (bundle / "Wander.exe").write_bytes(b"executable")
            internal = bundle / "_internal"
            internal.mkdir()
            (internal / "styles.css").write_text("body {}", encoding="utf-8")

            manifest = create_build_manifest(
                bundle,
                "0.8.0",
                "a" * 40,
                False,
            )

            self.assertEqual("0.8.0+aaaaaaaa", manifest["version_id"])
            self.assertEqual(2, len(manifest["files"]))
            self.assertEqual(manifest, verify_build_manifest(bundle))
            saved = json.loads((bundle / MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(manifest, saved)

    def test_dirty_build_identity_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            bundle = Path(temporary_directory)
            (bundle / "Wander.exe").write_bytes(b"executable")

            manifest = create_build_manifest(bundle, "0.8.0", "b" * 40, True)

            self.assertEqual("0.8.0+bbbbbbbb.dirty", manifest["version_id"])
            self.assertTrue(manifest["git_dirty"])

    def test_tampered_or_extra_files_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            bundle = Path(temporary_directory)
            executable = bundle / "Wander.exe"
            executable.write_bytes(b"original")
            create_build_manifest(bundle, "0.8.0", "c" * 40, False)
            executable.write_bytes(b"tampered")

            with self.assertRaisesRegex(ReleaseManifestError, "failed verification"):
                verify_build_manifest(bundle)

            executable.write_bytes(b"original")
            (bundle / "unexpected.dll").write_bytes(b"extra")
            with self.assertRaisesRegex(ReleaseManifestError, "file set"):
                verify_build_manifest(bundle)


if __name__ == "__main__":
    unittest.main()
