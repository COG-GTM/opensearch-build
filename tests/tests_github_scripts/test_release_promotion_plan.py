# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import importlib.util
import json
import os
import tempfile
import unittest
from typing import Any, Dict
from unittest.mock import patch

import yaml

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts")
SPEC = importlib.util.spec_from_file_location("release_promotion_plan", os.path.join(SCRIPTS, "release_promotion_plan.py"))
plan: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan)

INPUT_MANIFEST = os.path.join(os.path.dirname(__file__), "..", "..", "manifests", "3.9.0", "opensearch-3.9.0.yml")

BUILD_MANIFEST: Dict[str, Any] = {
    "build": {"name": "OpenSearch", "version": "3.9.0", "id": "12228"},
    "components": [
        {
            "name": "OpenSearch",
            "artifacts": {
                "dist": ["dist/opensearch-min-3.9.0-linux-x64.tar.gz"],
                "core-plugins": ["core-plugins/analysis-icu-3.9.0.zip", "core-plugins/repository-s3-3.9.0.zip"],
                "maven": [
                    "maven/org/opensearch/opensearch/maven-metadata.xml",
                    "maven/org/opensearch/opensearch/3.9.0/opensearch-3.9.0.jar",
                    "maven/org/opensearch/opensearch/3.9.0/opensearch-3.9.0.jar.sha1",
                    "maven/org/opensearch/opensearch/3.9.0/opensearch-3.9.0.pom",
                ],
            },
        },
        {
            "name": "job-scheduler",
            "artifacts": {
                "maven": ["maven/org/opensearch/opensearch-job-scheduler/3.9.0.0/opensearch-job-scheduler-3.9.0.0.jar"],
                "plugins": ["plugins/opensearch-job-scheduler-3.9.0.0.zip"],
            },
        },
    ],
}
BUNDLE_MANIFEST = {"build": {"location": "https://ci.opensearch.org/ci/dbc/x/dist/opensearch/opensearch-3.9.0-linux-x64.tar.gz"}}


class TestManifestFacts(unittest.TestCase):
    def test_facts(self) -> None:
        facts = plan.manifest_facts({"build": {"name": "OpenSearch Dashboards", "version": "2.19.5"}})
        self.assertEqual(facts["filename"], "opensearch-dashboards")
        self.assertEqual(facts["revision"], "2.19.5")
        self.assertEqual(facts["repo-version"], "2.x")
        self.assertEqual(facts["signing-email"], "opensearch@amazon.com")

    def test_qualifier_and_release_email(self) -> None:
        facts = plan.manifest_facts({"build": {"name": "OpenSearch", "version": "3.0.0", "qualifier": "beta1"}})
        self.assertEqual(facts["revision"], "3.0.0-beta1")
        self.assertEqual(facts["signing-email"], "release@opensearch.org")


class TestPromoteArtifacts(unittest.TestCase):
    def test_tar_plan(self) -> None:
        files = plan.with_checksums_and_signatures(
            plan.staged_files(BUILD_MANIFEST, BUNDLE_MANIFEST, "opensearch", "tar"), "opensearch", "3.9.0", "tar")
        objects = plan.upload_plan(files, BUILD_MANIFEST, "opensearch", "3.9.0", "linux", "x64", "tar")
        destinations = [o["destination"] for o in objects]
        self.assertEqual(len(destinations), 12)
        self.assertIn("releases/plugins/analysis-icu/3.9.0/analysis-icu-3.9.0.zip.sig", destinations)
        self.assertIn("releases/plugins/repository-s3/3.9.0/repository-s3-3.9.0.zip.sha512", destinations)
        self.assertIn("releases/core/opensearch/3.9.0/opensearch-min-3.9.0-linux-x64.tar.gz", destinations)
        self.assertIn("releases/bundle/opensearch/3.9.0/opensearch-3.9.0-linux-x64.tar.gz.sig", destinations)
        self.assertNotIn("releases/bundle/opensearch/3.9.0/opensearch-3.9.0-linux-x64.tar.gz.sha512.sig", destinations)

    def test_rpm_publishes_bundle_only(self) -> None:
        bundle = {"build": {"location": "https://x/dist/opensearch/opensearch-3.9.0-linux-arm64.rpm"}}
        files = plan.with_checksums_and_signatures(plan.staged_files(BUILD_MANIFEST, bundle, "opensearch", "rpm"), "opensearch", "3.9.0", "rpm")
        files += ["builds/opensearch/core-plugins/analysis-icu-3.9.0.zip"]
        objects = plan.upload_plan(files, BUILD_MANIFEST, "opensearch", "3.9.0", "linux", "arm64", "rpm")
        self.assertEqual([o["destination"] for o in objects], [
            "releases/bundle/opensearch/3.9.0/opensearch-3.9.0-linux-arm64.rpm",
            "releases/bundle/opensearch/3.9.0/opensearch-3.9.0-linux-arm64.rpm.sha512",
            "releases/bundle/opensearch/3.9.0/opensearch-3.9.0-linux-arm64.rpm.sig",
        ])

    def test_other_architecture_not_uploaded(self) -> None:
        objects = plan.upload_plan(["dist/opensearch/opensearch-3.9.0-linux-x64.tar.gz", "dist/opensearch/manifest.yml"],
                                   BUILD_MANIFEST, "opensearch", "3.9.0", "linux", "arm64", "tar")
        self.assertEqual(objects, [])

    def test_unknown_distribution(self) -> None:
        with self.assertRaises(SystemExit):
            plan.check_distribution("darwin", "tar")

    def test_local_root_run(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            for path in ["builds/opensearch/core-plugins/analysis-icu-3.9.0.zip", "builds/opensearch/core-plugins/analysis-icu-3.9.0.zip.sig",
                         "builds/opensearch/maven/org/opensearch/opensearch/3.9.0/opensearch-3.9.0.jar",
                         "dist/opensearch/opensearch-3.9.0-linux-x64.tar.gz", "dist/opensearch/manifest.yml"]:
                os.makedirs(os.path.dirname(os.path.join(root, path)), exist_ok=True)
                open(os.path.join(root, path), "w").close()
            with open(os.path.join(root, "builds/opensearch/manifest.yml"), "w") as handle:
                yaml.safe_dump(BUILD_MANIFEST, handle)
            out = os.path.join(root, "plan.json")
            summary = os.path.join(root, "summary.md")
            self.assertEqual(plan.main(["artifacts", "--job", "distribution-build-opensearch", "--build-number", "12228",
                                        "--input-manifest", INPUT_MANIFEST, "--platform", "linux", "--architecture", "x64",
                                        "--distribution", "tar", "--local-root", root, "--json", out, "--summary", summary]), 0)
            with open(out) as handle:
                objects = json.load(handle)["objects"]
        self.assertEqual([o["destination"] for o in objects], [
            "releases/plugins/analysis-icu/3.9.0/analysis-icu-3.9.0.zip",
            "releases/plugins/analysis-icu/3.9.0/analysis-icu-3.9.0.zip.sig",
            "releases/bundle/opensearch/3.9.0/opensearch-3.9.0-linux-x64.tar.gz",
        ])


class TestPromoteRepos(unittest.TestCase):
    FACTS = {"filename": "opensearch", "revision": "3.9.0", "repo-version": "3.x"}

    def test_yum(self) -> None:
        result = plan.repos_plan(self.FACTS, "distribution-build-opensearch", "12228", "yum", "https://ci")
        self.assertEqual(result["repo-prod-path"], "releases/bundle/opensearch/3.x/yum")
        self.assertEqual(result["packages"][1], {
            "source": "https://ci/distribution-build-opensearch/3.9.0/12228/linux/arm64/rpm/dist/opensearch/opensearch-3.9.0-linux-arm64.rpm",
            "destination": "releases/bundle/opensearch/3.x/yum/opensearch-3.9.0-linux-arm64.rpm",
        })

    def test_apt_pool(self) -> None:
        result = plan.repos_plan(self.FACTS, "distribution-build-opensearch", "12228", "apt", "https://ci")
        self.assertEqual(result["packages"][0]["destination"],
                         "releases/bundle/opensearch/3.x/apt/pool/main/o/opensearch/opensearch-3.9.0-linux-x64.deb")

    def test_bad_type(self) -> None:
        with self.assertRaises(SystemExit):
            plan.repos_plan(self.FACTS, "j", "1", "zypper", "https://ci")


class TestPromoteContainer(unittest.TestCase):
    def test_all_tags(self) -> None:
        copies = plan.docker_plan("opensearch:3.9.0.12228, opensearch-dashboards:3.9.0.9054", "3.9.0", True, True, True, True)
        self.assertEqual(len(copies), 12)
        self.assertEqual([c["DESTINATION_IMAGE"] for c in copies[:3]], ["opensearch:3.9.0", "opensearch:3", "opensearch:latest"])
        self.assertEqual(copies[3]["DESTINATION_IMAGE_REGISTRY"], "public.ecr.aws/opensearchorg/opensearchproject")
        self.assertEqual(copies[6]["SOURCE_IMAGE"], "opensearch-dashboards:3.9.0.9054")

    def test_flags_and_data_prepper(self) -> None:
        copies = plan.docker_plan("data-prepper:1.5.0-19", "1.5.0", True, False, False, False, "public.ecr.aws/x")
        self.assertEqual(copies, [{"SOURCE_IMAGE_REGISTRY": "public.ecr.aws/x", "SOURCE_IMAGE": "data-prepper:1.5.0-19",
                                   "DESTINATION_IMAGE_REGISTRY": "opensearchproject", "DESTINATION_IMAGE": "data-prepper:1.5.0"}])

    def test_parameters_check(self) -> None:
        with self.assertRaises(SystemExit):
            plan.docker_plan(" ", "3.9.0", True, True, True, True)
        with self.assertRaises(SystemExit):
            plan.docker_plan("opensearch:3.9.0.1", "", True, True, True, True)

    def test_main_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            output = os.path.join(root, "out")
            plan.main(["docker", "--source-images", "opensearch:3.9.0.1", "--release-version", "3.9.0", "--ecr-promote", "false",
                       "--tag-latest", "false", "--github-output", output, "--summary", os.path.join(root, "s")])
            with open(output) as handle:
                values = dict(line.rstrip("\n").split("=", 1) for line in handle)
        self.assertEqual(json.loads(values["copies"]), [
            {"SOURCE_IMAGE_REGISTRY": "opensearchstaging", "SOURCE_IMAGE": "opensearch:3.9.0.1",
             "DESTINATION_IMAGE_REGISTRY": "opensearchproject", "DESTINATION_IMAGE": "opensearch:3.9.0"},
            {"SOURCE_IMAGE_REGISTRY": "opensearchstaging", "SOURCE_IMAGE": "opensearch:3.9.0.1",
             "DESTINATION_IMAGE_REGISTRY": "opensearchproject", "DESTINATION_IMAGE": "opensearch:3"},
        ])

    def test_registry_digest(self) -> None:
        responses = [(200, b'{"token": "t"}', {}), (200, b"", {"docker-content-digest": "sha256:abc"})]
        with patch.object(plan, "http", side_effect=responses) as http:
            self.assertEqual(plan.registry_digest("public.ecr.aws/opensearchorg/opensearchproject/opensearch:3"), "sha256:abc")
        self.assertIn("scope=repository:opensearchorg/opensearchproject/opensearch:pull", http.call_args_list[0][0][0])
        self.assertEqual(http.call_args_list[1][0][0], "https://public.ecr.aws/v2/opensearchorg/opensearchproject/opensearch/manifests/3")


class TestPublishToMaven(unittest.TestCase):
    def test_files_and_coordinates(self) -> None:
        files = plan.maven_files(BUILD_MANIFEST)
        self.assertNotIn("maven/org/opensearch/opensearch/maven-metadata.xml", files)
        self.assertIn("maven/org/opensearch/opensearch/3.9.0/opensearch-3.9.0.pom.asc", files)
        self.assertNotIn("maven/org/opensearch/opensearch/3.9.0/opensearch-3.9.0.jar.sha1.asc", files)
        coordinates = plan.maven_coordinates(files)
        self.assertEqual([(c["group"], c["artifact"], c["version"], len(c["files"])) for c in coordinates], [
            ("org.opensearch", "opensearch", "3.9.0", 5),
            ("org.opensearch", "opensearch-job-scheduler", "3.9.0.0", 2),
        ])
