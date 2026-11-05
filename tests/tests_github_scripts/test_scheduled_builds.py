# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import hashlib
import json
import os
import tempfile
import unittest
from typing import Any, Dict, List

import yaml

from tests.tests_github_scripts import load_script

scheduled_builds = load_script("scheduled_builds")

ROOT = scheduled_builds.ROOT


def workflow_crons(workflow: str) -> List[str]:
    with open(os.path.join(ROOT, ".github", "workflows", f"{workflow}.yml")) as f:
        data = yaml.safe_load(f)
    # PyYAML reads the `on:` key as True
    triggers = data.get("on", data.get(True))
    return [entry["cron"] for entry in triggers["schedule"]]


def entries(workflow: str) -> List[Dict[str, Any]]:
    return [entry["params"] for entry in scheduled_builds.load_schedule(workflow)]


class TestScheduleDrift(unittest.TestCase):
    """The schedules must follow manifests/: a new active version without an entry would never be built."""

    def test_every_active_manifest_has_a_check_for_build_entry(self) -> None:
        scheduled = {(e["INPUT_MANIFEST"], e["TARGET_JOB_NAME"]) for e in entries("check-for-build")}
        active = scheduled_builds.active_manifests()
        self.assertTrue(active)
        for manifest in active:
            product = os.path.basename(manifest).rsplit("-", 1)[0]
            with self.subTest(manifest=manifest):
                self.assertIn((manifest, f"distribution-build-{product}"), scheduled,
                              f"manifests/{manifest} is active but .github/schedules/check-for-build.yml has no entry for it")

    def test_every_active_opensearch_manifest_has_a_min_snapshot_entry(self) -> None:
        scheduled = {e["INPUT_MANIFEST"] for e in entries("publish-min-snapshots")}
        for manifest in scheduled_builds.active_manifests():
            if not os.path.basename(manifest).startswith("opensearch-dashboards-"):
                with self.subTest(manifest=manifest):
                    self.assertIn(manifest, scheduled,
                                  f"manifests/{manifest} is active but .github/schedules/publish-min-snapshots.yml has no entry for it")

    def test_every_entry_resolves(self) -> None:
        for workflow in scheduled_builds.WORKFLOWS:
            for params in entries(workflow):
                with self.subTest(workflow=workflow, manifest=params["INPUT_MANIFEST"]):
                    scheduled_builds.normalize(workflow, params)

    def test_min_snapshot_entries_are_opensearch(self) -> None:
        for params in entries("publish-min-snapshots"):
            self.assertRegex(os.path.basename(params["INPUT_MANIFEST"]), r"^opensearch-[0-9]")

    def test_schedule_crons_match_the_workflow_triggers(self) -> None:
        for workflow in scheduled_builds.WORKFLOWS:
            with self.subTest(workflow=workflow):
                self.assertEqual(sorted(scheduled_builds.schedule_crons(workflow)), sorted(workflow_crons(workflow)))

    def test_entries_are_unique(self) -> None:
        for workflow in scheduled_builds.WORKFLOWS:
            keys = [(e["INPUT_MANIFEST"], e.get("TARGET_JOB_NAME", "")) for e in entries(workflow)]
            self.assertEqual(len(keys), len(set(keys)), workflow)


class TestActiveManifests(unittest.TestCase):
    def test_branch_refs_are_active(self) -> None:
        self.assertTrue(scheduled_builds.is_active({"components": [{"name": "a", "ref": "tags/3.8.0"}, {"name": "b", "ref": "main"}]}))
        self.assertTrue(scheduled_builds.is_active({"components": [{"name": "a", "ref": "3.8"}]}))

    def test_tags_and_commits_are_not_active(self) -> None:
        self.assertFalse(scheduled_builds.is_active({"components": [{"name": "a", "ref": "tags/3.8.0"}, {"name": "b", "ref": "a" * 40}]}))
        self.assertFalse(scheduled_builds.is_active({"components": [{"name": "a", "dist": "https://example.org/a.zip"}]}))

    def test_current_manifests(self) -> None:
        active = scheduled_builds.active_manifests()
        self.assertIn("3.10.0/opensearch-3.10.0.yml", active)
        self.assertIn("3.9.0/opensearch-dashboards-3.9.0.yml", active)
        self.assertNotIn("3.8.0/opensearch-3.8.0.yml", active)
        self.assertFalse(any(m.endswith("-test.yml") for m in active))


class TestResolve(unittest.TestCase):
    def test_schedule_picks_the_entries_of_the_cron(self) -> None:
        result = scheduled_builds.resolve("check-for-build", "schedule", "17 1 * * *", {})
        rows = result["matrix"]["include"]
        self.assertEqual(len(rows), len(entries("check-for-build")))
        self.assertFalse(result["live"])
        self.assertTrue(scheduled_builds.resolve("check-for-build", "schedule", "17 1 * * *", {}, live_var="true")["live"])

    def test_unknown_cron_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "no check-for-build schedule entry"):
            scheduled_builds.resolve("check-for-build", "schedule", "0 0 * * *", {})

    def test_pull_request_checks_everything_without_dispatching(self) -> None:
        result = scheduled_builds.resolve("publish-min-snapshots", "pull_request", "", {})
        self.assertFalse(result["live"])
        self.assertEqual(len(result["matrix"]["include"]), len(entries("publish-min-snapshots")))

    def test_check_for_build_opensearch_row(self) -> None:
        data = {"INPUT_MANIFEST": "3.9.0/opensearch-3.9.0.yml", "TARGET_JOB_NAME": "distribution-build-opensearch",
                "TEST_MANIFEST": "3.9.0/opensearch-3.9.0-test.yml", "BUILD_PLATFORM": "linux windows",
                "BUILD_DISTRIBUTION": "tar rpm deb zip", "TEST_PLATFORM": "linux", "TEST_DISTRIBUTION": "tar rpm",
                "UPDATE_GITHUB_ISSUE": False,
                "BUILD_ARGS": '{"COMPONENT_NAME":"OpenSearch","RUNS_ON":"[\\"ubuntu-24.04\\"]","OPTIONS":"no-upload"}'}
        result = scheduled_builds.resolve("check-for-build", "workflow_dispatch", "", data)
        self.assertTrue(result["live"])
        row = result["matrix"]["include"][0]
        self.assertEqual(row["manifest-path"], "manifests/3.9.0/opensearch-3.9.0.yml")
        self.assertEqual(row["job-name"], "distribution-build-opensearch")
        self.assertEqual(row["target"], "distribution-build-opensearch.yml")
        self.assertEqual(row["publishes"], "false")
        self.assertEqual(json.loads(row["inputs"]), {
            "INPUT_MANIFEST": "3.9.0/opensearch-3.9.0.yml",
            "TEST_MANIFEST": "3.9.0/opensearch-3.9.0-test.yml",
            "BUILD_PLATFORM": "linux windows",
            "BUILD_DISTRIBUTION": "tar rpm deb zip",
            "COMPONENT_NAME": "OpenSearch",
            "RUNS_ON": '["ubuntu-24.04"]',
            "OPTIONS": "test-platform=linux test-distribution=tar,rpm no-update-github-issue no-upload",
        })

    def test_check_for_build_dashboards_row(self) -> None:
        data = {"INPUT_MANIFEST": "3.9.0/opensearch-dashboards-3.9.0.yml", "TARGET_JOB_NAME": "distribution-build-opensearch-dashboards",
                "BUILD_PLATFORM": "linux", "TEST_PLATFORM": "linux", "TEST_DISTRIBUTION": "tar", "UPDATE_GITHUB_ISSUE": "false",
                "BUILD_DISTRIBUTION": "", "TEST_MANIFEST": "", "BUILD_ARGS": ""}
        row = scheduled_builds.resolve("check-for-build", "workflow_dispatch", "", data)["matrix"]["include"][0]
        self.assertEqual(row["publishes"], "true")
        self.assertEqual(json.loads(row["inputs"]), {
            "INPUT_MANIFEST": "3.9.0/opensearch-dashboards-3.9.0.yml",
            "BUILD_PLATFORM": "linux",
            "TEST_PLATFORM": "linux",
            "TEST_DISTRIBUTION": "tar",
            "OPTIONS": "update-github-issue=false",
        })

    def test_invalid_target_job(self) -> None:
        data = {"INPUT_MANIFEST": "3.9.0/opensearch-3.9.0.yml", "TARGET_JOB_NAME": "integ-test"}
        with self.assertRaisesRegex(ValueError, "Job integ-test is invalid"):
            scheduled_builds.resolve("check-for-build", "workflow_dispatch", "", data)

    def test_build_args_must_name_target_inputs(self) -> None:
        data = {"INPUT_MANIFEST": "3.9.0/opensearch-dashboards-3.9.0.yml", "TARGET_JOB_NAME": "distribution-build-opensearch-dashboards",
                "BUILD_ARGS": '{"RUNS_ON":"[\\"ubuntu-24.04\\"]"}'}
        with self.assertRaisesRegex(ValueError, "BUILD_ARGS key 'RUNS_ON'"):
            scheduled_builds.resolve("check-for-build", "workflow_dispatch", "", data)
        data["BUILD_ARGS"] = "[]"
        with self.assertRaisesRegex(ValueError, "JSON object"):
            scheduled_builds.resolve("check-for-build", "workflow_dispatch", "", data)

    def test_missing_manifest(self) -> None:
        with self.assertRaisesRegex(ValueError, "not found at manifests/9.9.9/opensearch-9.9.9.yml"):
            scheduled_builds.resolve("publish-min-snapshots", "workflow_dispatch", "", {"INPUT_MANIFEST": "9.9.9/opensearch-9.9.9.yml"})
        with self.assertRaisesRegex(ValueError, "not found"):
            scheduled_builds.resolve("publish-min-snapshots", "workflow_dispatch", "", {"INPUT_MANIFEST": "../manifests/3.9.0/opensearch-3.9.0.yml"})

    def test_self_dispatch_rows_turn_the_check_off(self) -> None:
        data = {"INPUT_MANIFEST": "3.9.0/opensearch-3.9.0.yml", "TARGETS": "linux-x64-tar", "PUBLISH": False, "CHECK_FOR_CHANGES": True}
        row = scheduled_builds.resolve("publish-min-snapshots", "workflow_dispatch", "", data)["matrix"]["include"][0]
        self.assertEqual(row["job-name"], "publish-opensearch-min-snapshots")
        self.assertEqual(row["target"], "publish-min-snapshots.yml")
        self.assertEqual(json.loads(row["inputs"]), {"INPUT_MANIFEST": "3.9.0/opensearch-3.9.0.yml", "TARGETS": "linux-x64-tar",
                                                     "PUBLISH": "False", "CHECK_FOR_CHANGES": "false"})
        self.assertEqual(row["publishes"], "false")

    def test_scheduled_rows_publish(self) -> None:
        for workflow, cron in (("check-for-build", "17 1 * * *"), ("publish-min-snapshots", "37 */4 * * *")):
            for row in scheduled_builds.resolve(workflow, "schedule", cron, {})["matrix"]["include"]:
                with self.subTest(workflow=workflow, manifest=row["input-manifest"]):
                    self.assertEqual(row["publishes"], "true")

    def test_feature_build_row(self) -> None:
        row = scheduled_builds.resolve("feature-build-opensearch", "schedule", "47 */4 * * *", {})["matrix"]["include"][0]
        self.assertEqual(row["manifest-path"], "feature-manifests/feature-datafusion/opensearch-3.9.0.yml")
        self.assertEqual(row["job-name"], "feature-build-opensearch")
        self.assertEqual(row["publishes"], "true")

    def test_unknown_parameter(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown publish-min-snapshots parameter 'NOPE'"):
            scheduled_builds.normalize("publish-min-snapshots", {"INPUT_MANIFEST": "3.9.0/opensearch-3.9.0.yml", "NOPE": "1"})


class TestManifestSha(unittest.TestCase):
    def test_sha_and_path(self) -> None:
        content = yaml.safe_dump({"schema-version": "1.1", "build": {"name": "OpenSearch", "version": "3.9.0"}, "components": []})
        with tempfile.TemporaryDirectory() as tmp:
            lock = os.path.join(tmp, "opensearch-3.9.0.yml.lock")
            with open(lock, "w", newline="\n") as f:
                f.write(content)
            with open(lock, "rb") as f:
                sha = hashlib.sha1(f.read()).hexdigest()
            self.assertEqual(scheduled_builds.manifest_sha(lock, "distribution-build-opensearch"), {
                "sha": sha, "version": "3.9.0", "path": f"distribution-build-opensearch/3.9.0/shas/{sha}.yml"})

    def test_s3_key_matches_jenkins(self) -> None:
        # getManifestSHA: "${inputManifestObj.getSHAsRoot(jobName)}/${sha1(lock)}.yml", getSHAsRoot = [jobName, build.version, 'shas'].join('/')
        sha = "14293cf0dbbfbac94d982cf0dda0e7811bc62cfa"
        self.assertEqual(scheduled_builds.sha_path("distribution-build-opensearch", "3.8.1", sha),
                         "distribution-build-opensearch/3.8.1/shas/14293cf0dbbfbac94d982cf0dda0e7811bc62cfa.yml")
        self.assertEqual(scheduled_builds.sha_path("publish-opensearch-min-snapshots", "3.8.1", sha),
                         f"publish-opensearch-min-snapshots/3.8.1/shas/{sha}.yml")
        for bad in (("distribution-build-opensearch", "3.8.1", "abc"), ("../x", "3.8.1", sha), ("job", "3.8.1/../..", sha)):
            with self.assertRaises(ValueError):
                scheduled_builds.sha_path(*bad)

    def test_s3_key_of_a_3_8_1_lock(self) -> None:
        manifest = {"schema-version": "1.2", "build": {"name": "OpenSearch", "version": "3.8.1"},
                    "components": [{"name": "OpenSearch", "repository": "https://github.com/opensearch-project/OpenSearch.git", "ref": "c" * 40}]}
        with tempfile.TemporaryDirectory() as tmp:
            lock = os.path.join(tmp, "opensearch-3.8.1.yml.lock")
            with open(lock, "w", newline="\n") as f:
                yaml.safe_dump(manifest, f)
            result = scheduled_builds.manifest_sha(lock, "distribution-build-opensearch")
        self.assertRegex(result["path"], r"^distribution-build-opensearch/3\.8\.1/shas/[0-9a-f]{40}\.yml$")
        self.assertEqual(result["path"], f"distribution-build-opensearch/3.8.1/shas/{result['sha']}.yml")

    def test_main_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "output")
            old = os.environ.get("GITHUB_OUTPUT")
            os.environ["GITHUB_OUTPUT"] = output
            try:
                code = scheduled_builds.main(["matrix", "--workflow", "check-for-build", "--event", "pull_request"])
            finally:
                if old is None:
                    del os.environ["GITHUB_OUTPUT"]
                else:
                    os.environ["GITHUB_OUTPUT"] = old
            self.assertEqual(code, 0)
            with open(output) as f:
                lines = dict(line.rstrip("\n").split("=", 1) for line in f)
            self.assertEqual(lines["live"], "false")
            self.assertEqual(len(json.loads(lines["matrix"])["include"]), len(entries("check-for-build")))

    def test_main_reports_errors(self) -> None:
        self.assertEqual(scheduled_builds.main(["matrix", "--workflow", "check-for-build", "--event", "schedule", "--schedule", "1 2 3 4 5"]), 1)
