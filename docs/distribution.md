# Native binary distribution

Release archives target DuckDB **1.4.5** or **1.5.5** and one of `linux_amd64`, `linux_arm64`, `osx_amd64`, or `osx_arm64`. Check `SELECT version();` and `PRAGMA platform;` in the consuming runtime before choosing an archive. Native extensions require an exact DuckDB version and platform match.

Linux builds use Ubuntu 22.04 (glibc 2.35 baseline) and require libcurl.so.4, libstdc++.so.6, and CA certificates. They do not target Alpine/musl. macOS builds target macOS 12 or later and use Apple system libcurl. Test the archive in your actual deployment image before shipping it.

## Install a release

Download the platform archive and its `.sha256` file from this repository's GitHub Releases. Private repository downloads require an authenticated GitHub account with access. Verify the checksum before extraction:

```sh
sha256sum -c jev-v0.1.0-duckdb-v1.5.5-linux_amd64.tar.gz.sha256
# macOS: shasum -a 256 -c <archive>.sha256
tar -xzf jev-v0.1.0-duckdb-v1.5.5-linux_amd64.tar.gz
```

Each archive contains the extension, a compatibility manifest, an SPDX 2.3 SBOM, documentation, the Apache 2.0 project license and NOTICE, and dependency license notices. Pin the release and checksum in deployment builds; install runtime libraries and copy the extension into the image. Supply credentials only at runtime, either through a DuckDB `jev` secret or `TYPESAFE_API_KEY`.

These binaries are unsigned. Enable unsigned extensions only in a trusted DuckDB runtime, then load the verified local file:

```python
import duckdb

connection = duckdb.connect(config={"allow_unsigned_extensions": True})
connection.execute("LOAD '/opt/duckdb/extensions/jev.duckdb_extension'")
```

Publishing to GitHub Releases does not sign an extension or register it in DuckDB's community repository.

## Release workflow

Run **Build and release native extension** with a new semantic version tag (for example `v0.1.0`), or push that tag. The workflow builds and tests all eight combinations of two DuckDB versions and four platforms without paid inference, packages each binary with checksums, and publishes only after all builds succeed. Manual dispatch creates the tag during publication. Existing published releases cannot be overwritten by the workflow; failed draft uploads can be retried.

The workflow badge reports GitHub's build status. Platform badges describe build targets, not a claim that every release has passed. Published archives also receive a GitHub build-provenance attestation; this establishes archive provenance but does not make the contained DuckDB extension a signed Community Extension.
