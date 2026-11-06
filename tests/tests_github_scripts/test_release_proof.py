# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import os
import tempfile
import unittest
from typing import Any, Dict

import yaml

from tests.tests_github_scripts import load_script

release_proof = load_script("release_proof")


def manifest(commits: Dict[str, str], version: str = "3.9.0") -> Dict[str, Any]:
    return {
        "build": {"name": "OpenSearch", "version": version, "platform": "linux", "architecture": "x64", "distribution": "tar", "id": "1"},
        "components": [{"name": name, "commit_id": commit} for name, commit in commits.items()],
    }


class TestReleaseProof(unittest.TestCase):
    def test_match(self) -> None:
        lines, differences = release_proof.compare(manifest({"OpenSearch": "a", "security": "b"}), manifest({"OpenSearch": "a", "security": "b", "k-NN": "c"}))
        self.assertEqual(differences, 0)
        self.assertIn("| security | `b` | `b` | match |", lines)

    def test_commit_differs(self) -> None:
        lines, differences = release_proof.compare(manifest({"OpenSearch": "a"}), manifest({"OpenSearch": "z"}))
        self.assertEqual(differences, 1)
        self.assertIn("| OpenSearch | `a` | `z` | DIFFERS |", lines)

    def test_missing_component_and_version(self) -> None:
        _, differences = release_proof.compare(manifest({"OpenSearch": "a"}, "3.9.1"), manifest({"OpenSearch": "a"}), ["OpenSearch", "security"])
        self.assertEqual(differences, 2)

    def test_main_reads_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            replay, jenkins = os.path.join(tmp, "replay.yml"), os.path.join(tmp, "jenkins.yml")
            for path, commit in ((replay, "a"), (jenkins, "a")):
                with open(path, "w") as f:
                    yaml.safe_dump(manifest({"OpenSearch": commit}), f)
            self.assertEqual(release_proof.main(["--replay", replay, "--jenkins", jenkins]), 0)
            with open(jenkins, "w") as f:
                yaml.safe_dump(manifest({"OpenSearch": "b"}), f)
            self.assertEqual(release_proof.main(["--replay", replay, "--jenkins", jenkins, "--components", "OpenSearch"]), 1)
