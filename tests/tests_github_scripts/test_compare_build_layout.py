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

compare_build_layout = load_script("compare_build_layout")

JENKINS_ROOT = "https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/3.9.0/12228/linux/x64/tar"
ACTIONS_ROOT = "https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/3.9.0-SNAPSHOT/9876543210/linux/x64/tar"
SHA_A = "a" * 40
SHA_B = "b" * 40


def build_manifest(version: str, build_id: str, sha: str, components: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema-version": "1.2",
        "build": {"name": "OpenSearch", "version": version, "platform": "linux", "architecture": "x64", "distribution": "tar", "id": build_id},
        "components": [
            {"name": name, "repository": f"https://github.com/opensearch-project/{name}.git", "ref": sha, "commit_id": sha, "artifacts": artifacts}
            for name, artifacts in components.items()
        ],
    }


def dist_manifest(version: str, build_id: str, root: str, sha: str, names: Any) -> Dict[str, Any]:
    return {
        "schema-version": "1.1",
        "build": {
            "name": "OpenSearch", "version": version, "platform": "linux", "architecture": "x64", "distribution": "tar",
            "location": f"{root}/dist/opensearch/opensearch-{version}-linux-x64.tar.gz", "id": build_id,
        },
        "components": [{"name": name, "repository": f"https://github.com/opensearch-project/{name}.git", "ref": sha, "commit_id": sha} for name in names],
    }


class TestCompareBuildLayout(unittest.TestCase):
    def setUp(self) -> None:
        core = lambda v: {"dist": [f"dist/opensearch-min-{v}-linux-x64.tar.gz"], "maven": [f"maven/org/opensearch/opensearch/{v}/opensearch-{v}.jar"]}  # noqa: E731
        self.jenkins = {
            f"{JENKINS_ROOT}/builds/opensearch/manifest.yml": yaml.safe_dump(build_manifest("3.9.0", "12228", SHA_A, {
                "OpenSearch": core("3.9.0"), "job-scheduler": {"plugins": ["plugins/opensearch-job-scheduler-3.9.0.0.zip"]}})),
            f"{JENKINS_ROOT}/dist/opensearch/manifest.yml": yaml.safe_dump(dist_manifest("3.9.0", "12228", JENKINS_ROOT, SHA_A, ["OpenSearch", "job-scheduler"])),
        }
        self.record = tempfile.mkdtemp()
        self.actions_build = build_manifest("3.9.0-SNAPSHOT", "9876543210", SHA_B, {"OpenSearch": core("3.9.0-SNAPSHOT")})
        self.write("builds/opensearch/manifest.yml", yaml.safe_dump(self.actions_build))
        self.write("dist/opensearch/manifest.yml", yaml.safe_dump(dist_manifest("3.9.0-SNAPSHOT", "9876543210", ACTIONS_ROOT, SHA_B, ["OpenSearch"])))
        self.files = [
            "builds/opensearch/dist/opensearch-min-3.9.0-SNAPSHOT-linux-x64.tar.gz",
            "builds/opensearch/manifest.yml",
            "builds/opensearch/maven/org/opensearch/opensearch/3.9.0-SNAPSHOT/opensearch-3.9.0-SNAPSHOT.jar",
            "dist/opensearch/manifest.yml",
            "dist/opensearch/opensearch-3.9.0-SNAPSHOT-linux-x64.tar.gz",
        ]
        self.write("file-list.txt", "\n".join(self.files) + "\n")

    def write(self, path: str, content: str) -> None:
        os.makedirs(os.path.dirname(os.path.join(self.record, path)), exist_ok=True)
        with open(os.path.join(self.record, path), "w") as handle:
            handle.write(content)

    def test_identical_after_normalisation(self) -> None:
        sections = compare_build_layout.compare(self.record, JENKINS_ROOT, self.jenkins.__getitem__)
        self.assertEqual(sections["shared components"], ["OpenSearch"])
        self.assertEqual(sections["file names"], [])
        self.assertEqual(sections["build manifest"], [])
        self.assertEqual(sections["bundle manifest"], [])

    def test_reports_missing_file_and_key(self) -> None:
        self.write("file-list.txt", "\n".join(self.files[1:]) + "\n")
        del self.actions_build["components"][0]["commit_id"]
        self.write("builds/opensearch/manifest.yml", yaml.safe_dump(self.actions_build))
        sections = compare_build_layout.compare(self.record, JENKINS_ROOT, self.jenkins.__getitem__)
        self.assertIn("-builds/opensearch/dist/opensearch-min-{version}-linux-x64.tar.gz", sections["file names"])
        self.assertIn("-components[0].commit_id: {sha}", sections["build manifest"])

    def test_maven_snapshot_naming_and_artifact_order(self) -> None:
        stamped = "maven/org/opensearch/opensearch/3.9.0-SNAPSHOT/opensearch-3.9.0-20261105.193311-1.jar"
        metadata = "maven/org/opensearch/opensearch/3.9.0-SNAPSHOT/maven-metadata.xml"
        plugin_metadata = "maven/org/opensearch/plugin/opensearch-geo/3.9.0.0-SNAPSHOT/maven-metadata.xml.sha1"
        artifacts = self.actions_build["components"][0]["artifacts"]
        artifacts["maven"] = [plugin_metadata, metadata, stamped]
        self.actions_build["components"][0]["version"] = "3.9.0.0-SNAPSHOT"
        self.write("builds/opensearch/manifest.yml", yaml.safe_dump(self.actions_build))
        files = [f for f in self.files if "/maven/" not in f] + [f"builds/opensearch/{p}" for p in (stamped, metadata, plugin_metadata)]
        self.write("file-list.txt", "\n".join(sorted(files)) + "\n")
        jenkins = yaml.safe_load(self.jenkins[f"{JENKINS_ROOT}/builds/opensearch/manifest.yml"])
        jenkins["components"][0]["version"] = "3.9.0.0"
        self.jenkins[f"{JENKINS_ROOT}/builds/opensearch/manifest.yml"] = yaml.safe_dump(jenkins)
        sections = compare_build_layout.compare(self.record, JENKINS_ROOT, self.jenkins.__getitem__)
        self.assertEqual(sections["snapshot-only files"], ["2 per-version maven-metadata.xml files"])
        self.assertEqual(sections["file names"], [])
        self.assertEqual(sections["build manifest"], [])
