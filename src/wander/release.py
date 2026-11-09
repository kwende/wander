from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from wander.store import SCHEMA_VERSION


MANIFEST_NAME = "wander-build.json"
MANIFEST_FORMAT = 1
VERSION_ID_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]*$")


class ReleaseManifestError(RuntimeError):
    pass


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bundle_files(bundle: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in bundle.rglob("*")
            if path.is_file() and path.name != MANIFEST_NAME
        ),
        key=lambda path: path.relative_to(bundle).as_posix(),
    )


def create_build_manifest(
    bundle_path: str | Path,
    app_version: str,
    git_sha: str,
    git_dirty: bool,
    entry_point: str = "Wander.exe",
) -> dict[str, Any]:
    bundle = Path(bundle_path).resolve()
    if not bundle.is_dir():
        raise ReleaseManifestError(f"Bundle directory does not exist: {bundle}")
    if not re.fullmatch(r"[0-9a-f]{40}", git_sha):
        raise ReleaseManifestError("git_sha must be a full 40-character lowercase commit SHA")
    entry = bundle / entry_point
    if not entry.is_file():
        raise ReleaseManifestError(f"Bundle entry point does not exist: {entry}")
    version_id = f"{app_version}+{git_sha[:8]}{'.dirty' if git_dirty else ''}"
    if not VERSION_ID_PATTERN.fullmatch(version_id):
        raise ReleaseManifestError(f"Unsafe bundle version identity: {version_id}")
    files = [
        {
            "path": path.relative_to(bundle).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in _bundle_files(bundle)
    ]
    manifest = {
        "format_version": MANIFEST_FORMAT,
        "version_id": version_id,
        "app_version": app_version,
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "entry_point": entry_point,
        "supported_schema": SCHEMA_VERSION,
        "build_runtime": {
            "python": platform.python_version(),
            "pyinstaller": _distribution_version("pyinstaller"),
            "pywebview": _distribution_version("pywebview"),
        },
        "files": files,
    }
    manifest_path = bundle / MANIFEST_NAME
    temporary = manifest_path.with_name(f".{manifest_path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, manifest_path)
    return manifest


def verify_build_manifest(bundle_path: str | Path) -> dict[str, Any]:
    bundle = Path(bundle_path).resolve()
    manifest_path = bundle / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseManifestError(f"Could not read bundle manifest: {manifest_path}") from error
    if manifest.get("format_version") != MANIFEST_FORMAT:
        raise ReleaseManifestError("Unsupported Wander bundle manifest format")
    version_id = manifest.get("version_id")
    if not isinstance(version_id, str) or not VERSION_ID_PATTERN.fullmatch(version_id):
        raise ReleaseManifestError("Bundle manifest has an unsafe version identity")
    entry_point = manifest.get("entry_point")
    if not isinstance(entry_point, str) or not (bundle / entry_point).is_file():
        raise ReleaseManifestError("Bundle manifest entry point is missing")
    expected_files = {
        item["path"]: item
        for item in manifest.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    actual_files = {
        path.relative_to(bundle).as_posix(): path for path in _bundle_files(bundle)
    }
    if set(actual_files) != set(expected_files):
        raise ReleaseManifestError("Bundle file set does not match its manifest")
    for relative_path, path in actual_files.items():
        expected = expected_files[relative_path]
        if path.stat().st_size != expected.get("size") or _sha256(path) != expected.get("sha256"):
            raise ReleaseManifestError(f"Bundle file failed verification: {relative_path}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or verify a Wander build manifest")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--bundle", type=Path, required=True)
    create.add_argument("--app-version", required=True)
    create.add_argument("--git-sha", required=True)
    create.add_argument("--dirty", action="store_true")
    create.add_argument("--entry-point", default="Wander.exe")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--bundle", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "create":
            result = create_build_manifest(
                arguments.bundle,
                arguments.app_version,
                arguments.git_sha,
                arguments.dirty,
                arguments.entry_point,
            )
        else:
            result = verify_build_manifest(arguments.bundle)
    except ReleaseManifestError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
