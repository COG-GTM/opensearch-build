# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import json
import os
import unittest
from typing import Any, Dict
from unittest.mock import patch

import pytest

from tests.tests_github_scripts import DATA_DIR, load_script

manifest_paths = load_script("manifest_paths")

INPUT_MANIFEST = os.path.join(DATA_DIR, "input-manifest.yml")
BUILD_MANIFEST = os.path.join(DATA_DIR, "build-manifest.yml")
REPO_MANIFEST = os.path.realpath(os.path.join(DATA_DIR, "..", "..", "..", "manifests", "3.9.0", "opensearch-3.9.0.yml"))
REPO_TEST_MANIFEST = REPO_MANIFEST.replace("opensearch-3.9.0.yml", "opensearch-3.9.0-test.yml")


class TestManifestPaths(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _capfd(self, capfd: Any) -> None:
        self.capfd = capfd

    def test_input_manifest_info(self) -> None:
        info = manifest_paths.input_manifest_info(manifest_paths.load(INPUT_MANIFEST))
        self.assertEqual(info["name"], "OpenSearch Dashboards")
        self.assertEqual(info["version"], "3.9.0")
        self.assertEqual(info["qualifier"], "alpha1")
        self.assertEqual(info["revision"], "3.9.0-alpha1")
        self.assertEqual(info["filename"], "opensearch-dashboards")
        self.assertEqual(json.loads(info["components"]), ["OpenSearch-Dashboards", "alertingDashboards", "securityDashboards"])

    def test_input_manifest_info_without_qualifier(self) -> None:
        info = manifest_paths.input_manifest_info(manifest_paths.load(REPO_MANIFEST))
        self.assertEqual(info["qualifier"], "")
        self.assertEqual(info["revision"], "3.9.0")

    def test_ci_image_drops_unsupported_container_options(self) -> None:
        image = manifest_paths.ci_image(manifest_paths.load(INPUT_MANIFEST), "input", "linux", "rpm")
        self.assertEqual(image["image"], "opensearchstaging/ci-runner:ci-runner-almalinux8-systemd-base-integtest-v1")
        self.assertEqual(image["args"], "-e JAVA_HOME=/opt/java/openjdk-21")
        self.assertEqual(image["java-version"], "openjdk-21")

    def test_ci_image_drops_inline_entrypoint(self) -> None:
        image = manifest_paths.ci_image(manifest_paths.load(INPUT_MANIFEST), "input", "linux", "tar")
        self.assertEqual(image["args"], "-e JAVA_HOME=/opt/java/openjdk-21")

    def test_ci_image_falls_back_to_default(self) -> None:
        image = manifest_paths.ci_image(manifest_paths.load(INPUT_MANIFEST), "input", "linux", "deb")
        self.assertEqual(image["image"], manifest_paths.DEFAULT_CI_IMAGE)
        self.assertEqual(image["java-version"], manifest_paths.DEFAULT_JAVA_VERSION)

    def test_ci_image_legacy_schema(self) -> None:
        manifest = {"schema-version": "1.0", "ci": {"image": {"name": "opensearchstaging/ci-runner:legacy", "args": "-e JAVA_HOME=/opt/java/openjdk-17"}}}
        image = manifest_paths.ci_image(manifest, "input", "linux", "tar")
        self.assertEqual(image["image"], "opensearchstaging/ci-runner:legacy")
        self.assertEqual(image["java-version"], "openjdk-17")

    def test_ci_image_rejects_injection(self) -> None:
        manifest = {"schema-version": "1.2", "ci": {"image": {"linux": {"tar": {"name": 'img", "options": "--privileged'}}}}}
        with self.assertRaises(ValueError):
            manifest_paths.ci_image(manifest, "input", "linux", "tar")
        with self.assertRaises(ValueError):
            manifest_paths.sanitize_container_options('-e "X=1"')

    def test_build_manifest_info(self) -> None:
        info = manifest_paths.build_manifest_info(manifest_paths.load(BUILD_MANIFEST), "distribution-build-opensearch", "11089", None, "https://ci.opensearch.org/ci/dbc")
        self.assertEqual(info["build-id"], "11089")
        self.assertEqual(info["package-name"], "opensearch-3.9.0-linux-arm64.tar.gz")
        self.assertEqual(info["min-artifact"], "dist/opensearch-min-3.9.0-linux-arm64.tar.gz")
        self.assertEqual(info["artifact-root"], "distribution-build-opensearch/3.9.0/11089/linux/arm64/tar")
        self.assertEqual(info["artifact-root-url"], "https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/3.9.0/11089/linux/arm64/tar")
        self.assertEqual(info["artifact-root-url-without-distribution"], "https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/3.9.0/11089/linux/arm64")
        self.assertEqual(info["index-file-root"], "distribution-build-opensearch/3.9.0/index/linux/arm64/tar")
        self.assertEqual(json.loads(info["components"]), ["OpenSearch", "common-utils"])
        self.assertEqual(json.loads(info["runs-on"]), ["self-hosted", "linux", "arm64", "al2023-arm64-m6g-4xlarge-docker-host"])

    def test_build_manifest_info_with_feature(self) -> None:
        info = manifest_paths.build_manifest_info(manifest_paths.load(BUILD_MANIFEST), "feature-build-opensearch", "7", "my-feature", "https://example.org")
        self.assertEqual(info["artifact-root"], "feature-build-opensearch/my-feature/7/linux/arm64/tar")

    def test_extension(self) -> None:
        self.assertEqual(manifest_paths.extension("zip"), "zip")
        self.assertEqual(manifest_paths.extension("rpm"), "rpm")
        self.assertEqual(manifest_paths.extension("tar"), "tar.gz")

    def test_runs_on_unknown_platform(self) -> None:
        with self.assertRaisesRegex(ValueError, "no runner pool for darwin-x64"):
            manifest_paths.runs_on("darwin", "x64")

    @patch("argparse._sys.argv", ["manifest_paths.py", "build-manifest-info", BUILD_MANIFEST, "--job-name", "distribution-build-opensearch"])
    def test_main_build_manifest_info_defaults_build_number(self) -> None:
        self.assertEqual(manifest_paths.main(), 0)
        out, _ = self.capfd.readouterr()
        self.assertIn("artifact-root=distribution-build-opensearch/3.9.0/11089/linux/arm64/tar\n", out)
        self.assertIn('runs-on=["self-hosted","linux","arm64","al2023-arm64-m6g-4xlarge-docker-host"]\n', out)

    @patch("argparse._sys.argv", ["manifest_paths.py", "input-manifest-info", INPUT_MANIFEST])
    def test_main_input_manifest_info(self) -> None:
        self.assertEqual(manifest_paths.main(), 0)
        out, _ = self.capfd.readouterr()
        self.assertIn("revision=3.9.0-alpha1\n", out)

    @patch("argparse._sys.argv", ["manifest_paths.py", "ci-image", REPO_MANIFEST, "--platform", "linux", "--distribution", "deb"])
    def test_main_ci_image(self) -> None:
        self.assertEqual(manifest_paths.main(), 0)
        out, _ = self.capfd.readouterr()
        self.assertIn("image=opensearchstaging/ci-runner:ci-runner-ubuntu2404-opensearch-build-v1\n", out)
        self.assertIn("java-version=openjdk-25\n", out)

    @patch("argparse._sys.argv", ["manifest_paths.py", "runs-on", "--platform", "windows", "--architecture", "x64"])
    def test_main_runs_on(self) -> None:
        self.assertEqual(manifest_paths.main(), 0)
        out, _ = self.capfd.readouterr()
        self.assertEqual(out, 'runs-on=["self-hosted","windows","x64","windows2019-x64-m5-4xlarge-docker-host"]\n')

    def test_integ_test_components_defaults_to_test_manifest(self) -> None:
        test_manifest = {"components": [{"name": "OpenSearch"}, {"name": "sql"}, {"name": "common-utils"}]}
        build_manifest = {"build": {"distribution": "tar"}, "components": [{"name": "OpenSearch"}, {"name": "common-utils"}]}
        self.assertEqual(manifest_paths.integ_test_components(test_manifest, build_manifest, ""), ["OpenSearch", "common-utils"])

    def test_integ_test_components_requested_and_package_exclusions(self) -> None:
        test_manifest = {"components": [{"name": "OpenSearch"}, {"name": "cross-cluster-replication"}, {"name": "query-insights"}]}
        build_manifest: Dict[str, Any] = {
            "build": {"distribution": "rpm"},
            "components": [{"name": "OpenSearch"}, {"name": "cross-cluster-replication"}, {"name": "query-insights"}],
        }
        self.assertEqual(manifest_paths.integ_test_components(test_manifest, build_manifest, " OpenSearch cross-cluster-replication "), ["OpenSearch"])
        build_manifest["build"]["distribution"] = "tar"
        self.assertEqual(
            manifest_paths.integ_test_components(test_manifest, build_manifest, "query-insights"),
            ["query-insights"],
        )

    def test_integ_test_components_rejects_unknown(self) -> None:
        with self.assertRaisesRegex(ValueError, "k-NN is not present in the test manifest"):
            manifest_paths.integ_test_components({"components": [{"name": "OpenSearch"}]}, {"build": {}, "components": []}, "k-NN")

    @patch("argparse._sys.argv", ["manifest_paths.py", "integ-test-components", REPO_TEST_MANIFEST, "--build-manifest", BUILD_MANIFEST])
    def test_main_integ_test_components(self) -> None:
        self.assertEqual(manifest_paths.main(), 0)
        out, _ = self.capfd.readouterr()
        self.assertIn('components=["OpenSearch"]\n', out)
        self.assertIn("has-components=true\n", out)

    @patch("argparse._sys.argv", ["manifest_paths.py", "ci-image", REPO_TEST_MANIFEST, "--kind", "test", "--distribution", "rpm", "--docker-run"])
    def test_main_ci_image_docker_run_keeps_entrypoint(self) -> None:
        self.assertEqual(manifest_paths.main(), 0)
        out, _ = self.capfd.readouterr()
        self.assertIn("--entrypoint=/usr/lib/systemd/systemd", out)
        self.assertIn("image=opensearchstaging/ci-runner:ci-runner-almalinux8-systemd-base-integtest-v1\n", out)
