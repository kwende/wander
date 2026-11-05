from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from wander.release import create_build_manifest


REPOSITORY = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")


@unittest.skipUnless(sys.platform == "win32" and POWERSHELL, "Windows installer test")
class DesktopInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.install_root = self.root / "install"
        self.data_root = self.root / "data-root"
        self.desktop = self.root / "desktop"
        self.programs = self.root / "programs"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def bundle(
        self,
        name: str,
        git_character: str,
        command: str = "@echo off\r\nexit /b 0\r\n",
        dirty: bool = False,
    ) -> Path:
        bundle = self.root / name
        bundle.mkdir()
        (bundle / "Wander.cmd").write_text(command, encoding="utf-8")
        create_build_manifest(
            bundle,
            "0.8.0",
            git_character * 40,
            dirty,
            entry_point="Wander.cmd",
        )
        return bundle

    def run_script(
        self,
        script: str,
        arguments: list[str],
        expect_success: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(REPOSITORY / "scripts" / script),
                *arguments,
            ],
            cwd=REPOSITORY,
            capture_output=True,
            text=True,
            check=False,
        )
        if expect_success and result.returncode != 0:
            self.fail(f"{script} failed:\nstdout={result.stdout}\nstderr={result.stderr}")
        if not expect_success and result.returncode == 0:
            self.fail(f"{script} unexpectedly succeeded:\n{result.stdout}")
        return result

    def install(self, bundle: Path, *extra: str) -> dict[str, object]:
        result = self.run_script(
            "install-desktop.ps1",
            [
                "-BundlePath",
                str(bundle),
                "-InstallRoot",
                str(self.install_root),
                "-DataRoot",
                str(self.data_root),
                "-DesktopDirectory",
                str(self.desktop),
                "-StartMenuDirectory",
                str(self.programs),
                "-SkipWebView2Check",
                "-AllowNewLedger",
                *extra,
            ],
        )
        return json.loads(result.stdout)

    def shortcut_target(self, path: Path) -> Path:
        command = (
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:WANDER_SHORTCUT_PATH);"
            "$s.TargetPath"
        )
        environment = os.environ.copy()
        environment["WANDER_SHORTCUT_PATH"] = str(path)
        result = subprocess.run(
            [str(POWERSHELL), "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            check=True,
            env=environment,
        )
        return Path(result.stdout.strip())

    def test_install_and_rollback_repoint_binaries_without_touching_data(self) -> None:
        first = self.bundle("first", "a")
        second = self.bundle("second", "b")

        self.install(first)
        second_result = self.install(second)

        state = json.loads((self.install_root / "install.json").read_text(encoding="utf-8-sig"))
        self.assertEqual("0.8.0+bbbbbbbb", state["active_version"])
        self.assertEqual("0.8.0+aaaaaaaa", state["previous_version"])
        self.assertFalse((self.data_root / "data" / "wander.db").exists())
        self.assertEqual("installed", second_result["action"])
        self.assertEqual(
            self.install_root / "versions" / "0.8.0+bbbbbbbb" / "Wander.cmd",
            self.shortcut_target(self.desktop / "Wander.lnk"),
        )

        rollback = self.run_script(
            "rollback-desktop.ps1",
            [
                "-InstallRoot",
                str(self.install_root),
            ],
        )

        rollback_result = json.loads(rollback.stdout)
        state = json.loads((self.install_root / "install.json").read_text(encoding="utf-8-sig"))
        self.assertEqual("0.8.0+aaaaaaaa", state["active_version"])
        self.assertEqual("0.8.0+bbbbbbbb", state["previous_version"])
        self.assertFalse(rollback_result["data_restored"])
        self.assertEqual(
            self.install_root / "versions" / "0.8.0+aaaaaaaa" / "Wander.cmd",
            self.shortcut_target(self.desktop / "Wander.lnk"),
        )

    def test_install_requires_existing_data_or_explicit_new_ledger(self) -> None:
        bundle = self.bundle("bundle", "c")

        result = self.run_script(
            "install-desktop.ps1",
            [
                "-BundlePath",
                str(bundle),
                "-InstallRoot",
                str(self.install_root),
                "-DataRoot",
                str(self.data_root),
                "-DesktopDirectory",
                str(self.desktop),
                "-StartMenuDirectory",
                str(self.programs),
                "-SkipWebView2Check",
            ],
            expect_success=False,
        )

        self.assertIn("No migrated Wander ledger exists", result.stderr)
        self.assertFalse(self.install_root.exists())

    def test_version_directory_is_immutable(self) -> None:
        bundle = self.bundle("bundle", "d")
        self.install(bundle)
        (bundle / "Wander.cmd").write_text("@echo off\r\necho changed\r\n", encoding="utf-8")
        create_build_manifest(bundle, "0.8.0", "d" * 40, False, "Wander.cmd")

        result = self.run_script(
            "install-desktop.ps1",
            [
                "-BundlePath",
                str(bundle),
                "-InstallRoot",
                str(self.install_root),
                "-DataRoot",
                str(self.data_root),
                "-DesktopDirectory",
                str(self.desktop),
                "-StartMenuDirectory",
                str(self.programs),
                "-SkipWebView2Check",
                "-AllowNewLedger",
            ],
            expect_success=False,
        )

        self.assertIn("differs from the supplied immutable bundle", result.stderr)

    def test_pointer_failure_restores_previous_state(self) -> None:
        first = self.bundle("first", "e")
        second = self.bundle("second", "f")
        self.install(first)
        previous_state = (self.install_root / "install.json").read_bytes()
        previous_target = self.shortcut_target(self.desktop / "Wander.lnk")
        invalid_programs = self.root / "not-a-directory"
        invalid_programs.write_text("occupied", encoding="utf-8")

        result = self.run_script(
            "install-desktop.ps1",
            [
                "-BundlePath",
                str(second),
                "-InstallRoot",
                str(self.install_root),
                "-DataRoot",
                str(self.data_root),
                "-DesktopDirectory",
                str(self.desktop),
                "-StartMenuDirectory",
                str(invalid_programs),
                "-SkipWebView2Check",
                "-AllowNewLedger",
            ],
            expect_success=False,
        )

        self.assertNotEqual("", result.stderr)
        self.assertEqual(previous_state, (self.install_root / "install.json").read_bytes())
        self.assertEqual(previous_target, self.shortcut_target(self.desktop / "Wander.lnk"))
        transactions = self.install_root / "transactions"
        self.assertEqual([], list(transactions.iterdir()) if transactions.exists() else [])

    def test_pending_pointer_transaction_is_recovered_after_interruption(self) -> None:
        bundle = self.bundle("bundle", "9")
        self.install(bundle)
        state_path = self.install_root / "install.json"
        shortcut_path = self.desktop / "Wander.lnk"
        previous_state = state_path.read_bytes()
        previous_target = self.shortcut_target(shortcut_path)
        environment = os.environ.copy()
        environment.update(
            {
                "WANDER_COMMON": str(REPOSITORY / "scripts" / "desktop-install-common.ps1"),
                "WANDER_INSTALL_ROOT": str(self.install_root),
                "WANDER_STATE_PATH": str(state_path),
                "WANDER_LINK_PATH": str(shortcut_path),
            }
        )
        interrupt = (
            ". $env:WANDER_COMMON;"
            "$null=Start-WanderPointerTransaction $env:WANDER_INSTALL_ROOT "
            "@($env:WANDER_LINK_PATH,$env:WANDER_STATE_PATH);"
            "Remove-Item -LiteralPath $env:WANDER_LINK_PATH -Force;"
            "Set-Content -LiteralPath $env:WANDER_STATE_PATH -Value 'interrupted'"
        )
        subprocess.run(
            [str(POWERSHELL), "-NoProfile", "-Command", interrupt],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        recover = (
            ". $env:WANDER_COMMON;"
            "Restore-WanderPointerTransactions $env:WANDER_INSTALL_ROOT"
        )

        subprocess.run(
            [str(POWERSHELL), "-NoProfile", "-Command", recover],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )

        self.assertEqual(previous_state, state_path.read_bytes())
        self.assertEqual(previous_target, self.shortcut_target(shortcut_path))
        transactions = self.install_root / "transactions"
        self.assertEqual([], list(transactions.iterdir()) if transactions.exists() else [])

    def test_rollback_refuses_incompatible_data_and_leaves_pointers_unchanged(self) -> None:
        incompatible = self.bundle(
            "incompatible",
            "1",
            '@echo off\r\n> %4 echo {"status":"ok","compatible":false}\r\nexit /b 2\r\n',
        )
        current = self.bundle("current", "2")
        self.install(incompatible)
        self.install(current)
        database = self.data_root / "data" / "wander.db"
        database.parent.mkdir(parents=True)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("PRAGMA user_version = 5")
        previous_state = (self.install_root / "install.json").read_bytes()

        result = self.run_script(
            "rollback-desktop.ps1",
            [
                "-InstallRoot",
                str(self.install_root),
                "-DesktopDirectory",
                str(self.desktop),
                "-StartMenuDirectory",
                str(self.programs),
            ],
            expect_success=False,
        )

        self.assertIn("compatibility check failed", result.stderr)
        self.assertEqual(previous_state, (self.install_root / "install.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
