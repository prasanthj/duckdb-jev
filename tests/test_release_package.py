"""Release archive contract, checked against the locally built native extension."""

import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import duckdb


def test_release_archive_loads_and_contains_verifiable_metadata() -> None:
    root = Path(__file__).resolve().parents[1]
    with duckdb.connect() as connection:
        result = connection.execute("PRAGMA platform").fetchone()
    assert result is not None
    platform = str(result[0])
    subprocess.run(
        [sys.executable, "scripts/package_release.py", "--tag", "v0.0.0-test", "--platform", platform],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    archive = root / "dist" / f"jev-v0.0.0-test-duckdb-v1.5.5-{platform}.tar.gz"
    checksum = archive.with_suffix(".gz.sha256")
    try:
        assert checksum.read_text() == f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
        with tarfile.open(archive) as package:
            assert set(package.getnames()) == {
                "jev.duckdb_extension", "manifest.json", "README.md", "DISTRIBUTION.md",
                "LICENSE.duckdb", "LICENSE.nlohmann-json",
            }
            metadata = package.extractfile("manifest.json")
            binary = package.extractfile("jev.duckdb_extension")
            assert metadata is not None and binary is not None
            manifest = json.load(metadata)
            assert manifest["platform"] == platform
            assert manifest["duckdb_version"] == "1.5.5"
            assert manifest["release"] == "v0.0.0-test"
            assert manifest["signed"] is False
            assert manifest["sha256"] == hashlib.sha256(binary.read()).hexdigest()
    finally:
        archive.unlink(missing_ok=True)
        checksum.unlink(missing_ok=True)
