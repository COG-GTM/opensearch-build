# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import os
import tempfile
import unittest
from typing import List
from unittest.mock import patch

import yaml

from tests.tests_github_scripts import load_script

localize_bundle_locations = load_script("localize_bundle_locations")

URL = "https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/3.8.1/42141323087/linux/x64"


def write_bundle(root: str, distribution: str, files: List[str]) -> str:
    ext = "tar.gz" if distribution == "tar" else distribution
    dist = f"dist/opensearch/opensearch-3.8.1-linux-x64.{ext}"
    plugin = "builds/opensearch/plugins/opensearch-system-templates-3.8.1.0.zip"
    for path in files:
        os.makedirs(os.path.dirname(os.path.join(root, path)), exist_ok=True)
        open(os.path.join(root, path), "w").close()
    manifest = os.path.join(root, "dist", "opensearch", "manifest.yml")
    os.makedirs(os.path.dirname(manifest), exist_ok=True)
    with open(manifest, "w") as f:
        yaml.safe_dump({
            "build": {"name": "OpenSearch", "distribution": distribution, "location": f"{URL}/{distribution}/{dist}"},
            "components": [{"name": "opensearch-system-templates", "location": f"{URL}/{distribution}/{plugin}"}],
        }, f)
    return manifest


class TestLocalizeBundleLocations(unittest.TestCase):
    def test_tar_locations_point_at_the_local_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "tar")
            files = ["dist/opensearch/opensearch-3.8.1-linux-x64.tar.gz", "builds/opensearch/plugins/opensearch-system-templates-3.8.1.0.zip"]
            manifest = write_bundle(root, "tar", files)
            with patch("builtins.print"):
                self.assertEqual(localize_bundle_locations.main([root, "--platform", "linux", "--architecture", "x64", "--distribution", "tar"]), 0)
            with open(manifest) as f:
                bundle = yaml.safe_load(f)
        self.assertEqual(bundle["build"]["location"], f"{root}/{files[0]}")
        self.assertEqual(bundle["components"][0]["location"], f"{root}/{files[1]}")

    def test_deb_locations_point_at_the_local_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "deb")
            files = ["dist/opensearch/opensearch-3.8.1-linux-x64.deb", "builds/opensearch/plugins/opensearch-system-templates-3.8.1.0.zip"]
            manifest = write_bundle(root, "deb", files)
            with patch("builtins.print"):
                rewritten = localize_bundle_locations.localize(root, "linux", "x64", "deb")
            with open(manifest) as f:
                bundle = yaml.safe_load(f)
        self.assertEqual(rewritten, [f"{root}/{path}" for path in files])
        self.assertEqual(bundle["build"]["location"], f"{root}/{files[0]}")

    def test_missing_local_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "tar")
            write_bundle(root, "tar", ["dist/opensearch/opensearch-3.8.1-linux-x64.tar.gz"])
            with patch("builtins.print") as printed:
                self.assertEqual(localize_bundle_locations.main([root, "--platform", "linux", "--architecture", "x64", "--distribution", "tar"]), 1)
        self.assertIn("::error::1 rewritten bundle location(s)", printed.call_args_list[-1].args[0])

    def test_other_cells_are_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "tar")
            manifest = write_bundle(root, "tar", [])
            with open(manifest) as f:
                before = f.read()
            self.assertEqual(localize_bundle_locations.localize(root, "linux", "arm64", "tar"), [])
            with open(manifest) as f:
                self.assertEqual(f.read(), before)
