# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import hashlib
import os
import tempfile
import unittest
from typing import Any, Dict, List

import yaml

from tests.tests_github_scripts import load_script

min_snapshots = load_script("min_snapshots")


def write_build(root: str, platform: str, architecture: str, distribution: str, plugins: List[str]) -> str:
    build_dir = os.path.join(root, distribution, "builds", "opensearch")
    os.makedirs(os.path.join(build_dir, "dist"))
    ext = "tar.gz" if distribution == "tar" else distribution
    with open(os.path.join(build_dir, "dist", f"opensearch-min-3.10.0-SNAPSHOT-{platform}-{architecture}.{ext}"), "wb") as f:
        f.write(b"min")
    artifacts: Dict[str, Any] = {"dist": [f"dist/opensearch-min-3.10.0-SNAPSHOT-{platform}-{architecture}.{ext}"]}
    if plugins:
        os.makedirs(os.path.join(build_dir, "core-plugins"))
        artifacts["core-plugins"] = [f"core-plugins/{name}-3.10.0-SNAPSHOT.zip" for name in plugins]
        for name in plugins:
            for suffix in (".zip", ".zip.sha512", ".zip.sig"):
                with open(os.path.join(build_dir, "core-plugins", f"{name}-3.10.0-SNAPSHOT{suffix}"), "w") as f:
                    f.write(name)
    manifest = {
        "schema-version": "1.2",
        "build": {"name": "OpenSearch", "version": "3.10.0-SNAPSHOT", "platform": platform, "architecture": architecture, "distribution": distribution, "id": "1"},
        "components": [{"name": "OpenSearch", "artifacts": artifacts}],
    }
    path = os.path.join(build_dir, "manifest.yml")
    with open(path, "w") as f:
        yaml.safe_dump(manifest, f)
    return path


def staged(staging: str) -> List[str]:
    return sorted(os.path.relpath(os.path.join(d, n), staging).replace(os.sep, "/") for d, _, names in os.walk(staging) for n in names)


class TestMinSnapshots(unittest.TestCase):
    def test_linux_x64_tar_with_core_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            manifest = write_build(root, "linux", "x64", "tar", ["analysis-icu", "analysis-icu-extra"])
            staging = os.path.join(root, "staging")
            out = min_snapshots.prepare(manifest, staging)
            self.assertEqual(out["core-plugins"], "true")
            self.assertEqual(out["core-plugin-count"], "2")
            self.assertEqual(out["signing-email"], "release@opensearch.org")
            core = "core/opensearch/3.10.0-SNAPSHOT/opensearch-min-3.10.0-SNAPSHOT-linux-x64-latest.tar.gz"
            files = staged(staging)
            self.assertIn(core, files)
            self.assertIn(f"{core}.sha512", files)
            self.assertIn(f"{core}.build-manifest.yml", files)
            # includePathPattern "**/<plugin>*" is a prefix match, so analysis-icu also picks up analysis-icu-extra
            self.assertIn("plugins/analysis-icu/3.10.0-SNAPSHOT/analysis-icu-extra-3.10.0-SNAPSHOT.zip.sig", files)
            self.assertIn("plugins/analysis-icu-extra/3.10.0-SNAPSHOT/analysis-icu-extra-3.10.0-SNAPSHOT.zip", files)
            self.assertNotIn("plugins/analysis-icu-extra/3.10.0-SNAPSHOT/analysis-icu-3.10.0-SNAPSHOT.zip", files)
            with open(os.path.join(staging, f"{core}.sha512")) as f:
                expected = hashlib.sha512(b"min").hexdigest()
                self.assertEqual(f.read(), f"{expected}  opensearch-min-3.10.0-SNAPSHOT-linux-x64-latest.tar.gz\n")

    def test_windows_zip_has_no_core_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            manifest = write_build(root, "windows", "x64", "zip", ["analysis-icu"])
            staging = os.path.join(root, "staging")
            out = min_snapshots.prepare(manifest, staging)
            self.assertEqual(out["core-plugins"], "false")
            self.assertEqual(staged(staging), [
                "core/opensearch/3.10.0-SNAPSHOT/opensearch-min-3.10.0-SNAPSHOT-windows-x64-latest.zip",
                "core/opensearch/3.10.0-SNAPSHOT/opensearch-min-3.10.0-SNAPSHOT-windows-x64-latest.zip.build-manifest.yml",
                "core/opensearch/3.10.0-SNAPSHOT/opensearch-min-3.10.0-SNAPSHOT-windows-x64-latest.zip.sha512",
            ])

    def test_missing_min_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            manifest = write_build(root, "linux", "arm64", "tar", [])
            os.remove(os.path.join(os.path.dirname(manifest), "dist", "opensearch-min-3.10.0-SNAPSHOT-linux-arm64.tar.gz"))
            with self.assertRaises(FileNotFoundError):
                min_snapshots.prepare(manifest, os.path.join(root, "staging"))
