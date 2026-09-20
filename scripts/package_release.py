"""Validate the native artifact and package it with compatibility metadata."""

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]


def release_tag(value: str) -> str:
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?", value):
        raise argparse.ArgumentTypeError("Expected a release tag such as v0.1.0")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, type=release_tag)
    parser.add_argument("--platform", required=True, choices=["linux_amd64", "linux_arm64", "osx_amd64", "osx_arm64"])
    args = parser.parse_args()
    binary = ROOT / "build/extension/jev/jev.duckdb_extension"
    # The smoke query is deliberately null and must not perform inference.
    # Supply a non-secret placeholder because extension configuration still
    # validates credentials, and point any regression at closed loopback.
    os.environ["TYPESAFE_API_KEY"] = "package-smoke-never-sent"
    with duckdb.connect(config={"allow_unsigned_extensions": True}) as con:
        actual = con.execute("PRAGMA platform").fetchone()
        if actual != (args.platform,) or duckdb.__version__ != "1.5.5":
            raise RuntimeError(f"Runtime mismatch: DuckDB {duckdb.__version__}, platform {actual}")
        con.execute(f"LOAD '{binary}'")
        con.execute("SET jev_endpoint = 'http://127.0.0.1:1/v1/systemone'")
        if con.execute("SELECT jev_noul(NULL,'no API call')").fetchone() != (None,):
            raise RuntimeError("Native smoke check failed")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    manifest = {
        "extension": "jev", "release": args.tag, "duckdb_version": duckdb.__version__,
        "platform": args.platform, "architecture": platform.machine(),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "sha256": digest, "signed": False,
        "minimum_os": "glibc 2.35 (Ubuntu 22.04 baseline)" if args.platform.startswith("linux") else "macOS 12",
        "runtime_dependencies": ["libcurl.so.4", "libstdc++.so.6", "CA certificates"] if args.platform.startswith("linux")
                                else ["Apple system libcurl", "CA certificates"],
    }
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = output / f"jev-{args.tag}-duckdb-v{duckdb.__version__}-{args.platform}.tar.gz"
    with tempfile.TemporaryDirectory() as temporary:
        metadata = Path(temporary) / "manifest.json"
        metadata.write_text(json.dumps(manifest, indent=2) + "\n")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(binary, arcname="jev.duckdb_extension")
            tar.add(metadata, arcname="manifest.json")
            tar.add(ROOT / "README.md", arcname="README.md")
            tar.add(ROOT / "docs/distribution.md", arcname="DISTRIBUTION.md")
            tar.add(ROOT / "vendor/duckdb/LICENSE", arcname="LICENSE.duckdb")
            tar.add(ROOT / "src/include/LICENSE.nlohmann-json", arcname="LICENSE.nlohmann-json")
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
    )
    print(archive)


if __name__ == "__main__":
    main()
