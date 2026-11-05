# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import json
import os
import tempfile
import unittest
from typing import Any, Dict
from unittest.mock import patch

import yaml

from tests.tests_github_scripts import load_script

benchmark_test = load_script("benchmark_test")

WORKFLOWS = os.path.join(benchmark_test.ROOT, ".github", "workflows")

MANIFEST = {
    "build": {"id": "11111", "architecture": "x64", "version": "3.9.0"},
    "components": [{"name": "OpenSearch", "commit_id": "abc123"}, {"name": "security", "commit_id": "def456"}],
}


def resolve(workflow: str, mode: str, data: Dict[str, Any], schedule: str = "", suite: str = "") -> Dict[str, Any]:
    result: Dict[str, Any] = benchmark_test.resolve(workflow, mode, data, schedule, "42", "1", suite, manifest_reader=lambda url: MANIFEST)
    return result


class TestSchedules(unittest.TestCase):
    def test_workflow_crons_match_schedule_files(self) -> None:
        for workflow in ("benchmark-test", "benchmark-test-endpoint", "benchmark-pull-request"):
            with open(os.path.join(WORKFLOWS, f"{workflow}.yml")) as f:
                on = yaml.safe_load(f)[True]
            crons = sorted(item["cron"] for item in on["schedule"])
            self.assertEqual(crons, sorted(benchmark_test.schedule_crons(workflow)), workflow)

    def test_entry_counts_match_jenkins(self) -> None:
        counts: Dict[str, int] = {}
        for workflow in ("benchmark-test", "benchmark-test-endpoint", "benchmark-pull-request"):
            for entry in benchmark_test.load_schedule(workflow):
                counts[entry["suite"]] = counts.get(entry["suite"], 0) + 1
        self.assertEqual(counts, {"benchmark-test": 96, "benchmark-test-vectorsearch": 56, "benchmark-test-datafusion": 12,
                                  "benchmark-test-endpoint": 21, "benchmark-pull-request": 11})

    def test_schedule_files_are_current(self) -> None:
        jenkinsfile = os.path.join(benchmark_test.ROOT, "jenkins", "opensearch", "benchmark-test.jenkinsfile")
        if not os.path.exists(jenkinsfile):
            self.skipTest("jenkins/ removed at cutover")
        for workflow in ("benchmark-test", "benchmark-test-endpoint", "benchmark-pull-request"):
            self.assertEqual(benchmark_test.import_jenkins(workflow)["entries"], benchmark_test.load_schedule(workflow), workflow)

    def test_every_schedule_resolves_within_the_matrix_limit(self) -> None:
        for workflow in ("benchmark-test", "benchmark-test-endpoint", "benchmark-pull-request"):
            for cron in benchmark_test.schedule_crons(workflow):
                rows = resolve(workflow, "schedule", {}, cron)["matrix"]["include"]
                self.assertTrue(0 < len(rows) <= benchmark_test.MAX_MATRIX)
                self.assertEqual(len({r["suffix"] for r in rows}), len(rows))

    def test_vectorsearch_runs_on_the_vector_pool(self) -> None:
        rows = resolve("benchmark-test", "schedule", {}, "17 3 * * *")["matrix"]["include"]
        self.assertEqual({r["suite"] for r in rows}, {"benchmark-test-vectorsearch"})
        self.assertEqual({r["runs-on"] for r in rows}, {json.dumps(benchmark_test.VECTOR_BENCHMARK_LABELS)})

    def test_unknown_cron(self) -> None:
        with self.assertRaises(ValueError):
            resolve("benchmark-test", "schedule", {}, "1 1 1 1 1")


class TestResolve(unittest.TestCase):
    def test_bundle_manifest_with_security_runs_both_variants(self) -> None:
        rows = resolve("benchmark-test", "inputs", {"SUITE": "benchmark-test", "BUNDLE_MANIFEST_URL": "https://ci.example/manifest.yml",
                                                    "TEST_WORKLOAD": "big5", "USER_TAGS": "run-type:adhoc", "OPTIONS": ""})["matrix"]["include"]
        self.assertEqual([(r["suffix"], r["insecure"]) for r in rows], [("42-1-secure", False), ("42-1", True)])
        self.assertEqual(rows[0]["user-tag"], "distribution-build-id:11111,arch:x64,os-commit-id:abc123,run-type:adhoc,"
                                              "security-enabled:true,jenkins-build-id:42-1")
        self.assertEqual(json.loads(rows[1]["params"])["BUILD_ID"], "11111")

    def test_distribution_url_runs_without_security(self) -> None:
        rows = resolve("benchmark-test", "inputs", {"DISTRIBUTION_URL": "https://a/os.tar.gz", "DISTRIBUTION_VERSION": "3.9.0",
                                                    "SINGLE_NODE_CLUSTER": True, "OPTIONS": "MIN_DISTRIBUTION=true;DATA_NODE_COUNT=3"})["matrix"]["include"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["insecure"])
        params = json.loads(rows[0]["params"])
        self.assertEqual(params["MIN_DISTRIBUTION"], "true")
        self.assertEqual(params["DATA_NODE_COUNT"], "3")
        self.assertEqual(rows[0]["user-tag"], "security-enabled:false,jenkins-build-id:42-1")

    def test_benchmark_test_needs_a_distribution(self) -> None:
        with self.assertRaises(ValueError):
            resolve("benchmark-test", "inputs", {"TEST_WORKLOAD": "big5"})

    def test_unknown_parameter_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve("benchmark-test", "inputs", {"DISTRIBUTION_URL": "https://a", "DISTRIBUTION_VERSION": "1", "OPTIONS": "NOPE=1"})
        with self.assertRaises(ValueError):
            # PLUGIN_URL is not a benchmark-test-vectorsearch parameter
            resolve("benchmark-test", "inputs", {"DISTRIBUTION_URL": "https://a", "DISTRIBUTION_VERSION": "1", "PLUGIN_URL": "https://p"},
                    suite="benchmark-test-vectorsearch")

    def test_shell_metacharacters_are_rejected(self) -> None:
        for value in ["x'; rm -rf /", "$(id)", "`id`", "a\nb", "a|b"]:
            with self.assertRaises(ValueError, msg=value):
                resolve("benchmark-pull-request", "payload", {"DISTRIBUTION_URL": "https://a", "DISTRIBUTION_VERSION": "1", "TEST_PROCEDURE": value})

    def test_payload(self) -> None:
        result = resolve("benchmark-pull-request", "payload", {
            "pull_request_number": "12345", "repository": "opensearch-project/OpenSearch",
            "baseline_cluster_config": "x64-r5.xlarge-single-node-1-shard-0-replica-baseline",
            "DISTRIBUTION_URL": "https://a/os.tar.gz", "DISTRIBUTION_VERSION": "3.9.0", "SECURITY_ENABLED": "false",
            "USER_TAGS": "pull_request_number:12345", "WORKLOAD_PARAMS": '{"number_of_shards":"1"}', "dry_run": True,
            "OPTIONS": "MIN_DISTRIBUTION=true;DATA_INSTANCE_TYPE=r5.xlarge"})
        self.assertEqual(result["pull-request-number"], "12345")
        self.assertEqual(result["repository"], "opensearch-project/OpenSearch")
        self.assertEqual(result["dry-run"], "true")
        row = result["matrix"]["include"][0]
        self.assertEqual(row["user-tag"], "pull_request_number:12345,security-enabled:false")
        self.assertEqual(json.loads(row["params"])["TEST_WORKLOAD"], "nyc_taxis")
        self.assertEqual(json.loads(row["params"])["MIN_DISTRIBUTION"], "true")
        self.assertEqual(json.loads(row["params"])["DATA_INSTANCE_TYPE"], "r5.xlarge")

    def test_dry_run_payload_fits_repository_dispatch(self) -> None:
        with open(os.path.join(WORKFLOWS, "benchmark-dry-run.yml")) as f:
            payload = json.loads(yaml.safe_load(f)["jobs"]["benchmark-pull-request"]["with"]["client-payload"])
        self.assertLessEqual(len(payload), 10)
        self.assertEqual(resolve("benchmark-pull-request", "payload", payload)["dry-run"], "true")

    def test_json_params_are_compacted(self) -> None:
        row = resolve("benchmark-pull-request", "payload", {"DISTRIBUTION_URL": "https://a", "DISTRIBUTION_VERSION": "1",
                                                            "WORKLOAD_PARAMS": '{"number_of_shards": 1, "k": "v"}'})["matrix"]["include"][0]
        self.assertEqual(json.loads(row["params"])["WORKLOAD_PARAMS"], '{"number_of_shards":1,"k":"v"}')
        row = resolve("benchmark-pull-request", "payload", {"DISTRIBUTION_URL": "https://a", "DISTRIBUTION_VERSION": "1",
                                                            "WORKLOAD_PARAMS": "number_of_replicas:1,number_of_shards:5"})["matrix"]["include"][0]
        self.assertEqual(json.loads(row["params"])["WORKLOAD_PARAMS"], "number_of_replicas:1,number_of_shards:5")
        for bad in ["{bad", '{"k": "a b"}', "a:1, b:2"]:
            with self.assertRaises(ValueError, msg=bad):
                resolve("benchmark-pull-request", "payload", {"DISTRIBUTION_URL": "https://a", "DISTRIBUTION_VERSION": "1", "WORKLOAD_PARAMS": bad})

    def test_payload_validation(self) -> None:
        base = {"DISTRIBUTION_URL": "https://a", "DISTRIBUTION_VERSION": "1"}
        for extra in [{"pull_request_number": "12a"}, {"repository": "not a repo"}, {"baseline_cluster_config": "a;b"}]:
            with self.assertRaises(ValueError, msg=str(extra)):
                resolve("benchmark-pull-request", "payload", dict(base, **extra))
        with self.assertRaises(ValueError):
            resolve("benchmark-pull-request", "payload", {"DISTRIBUTION_URL": "https://a"})

    def test_endpoint(self) -> None:
        row = resolve("benchmark-test-endpoint", "inputs", {"CLUSTER_ENDPOINT": "vpc-x.us-east-1.es.amazonaws.com", "SECURITY_ENABLED": True,
                                                            "SIGV4": True, "SERVICE": "", "CREDENTIALS_KEY": "", "OPTIONS": ""})["matrix"]["include"][0]
        self.assertFalse(row["insecure"])
        self.assertEqual(row["user-tag"], "security-enabled:true")
        with self.assertRaises(ValueError):
            resolve("benchmark-test-endpoint", "inputs", {"SIGV4": True})

    def test_rerun_suffix_has_the_attempt(self) -> None:
        rows = benchmark_test.resolve("benchmark-pull-request", "inputs", {"DISTRIBUTION_URL": "https://a", "DISTRIBUTION_VERSION": "1"},
                                      "", "42", "2")["matrix"]["include"]
        self.assertEqual(rows[0]["suffix"], "42-2-1")


class TestCommand(unittest.TestCase):
    def test_execute_test_args(self) -> None:
        params = {"DISTRIBUTION_URL": "https://a/os.tar.gz", "DISTRIBUTION_VERSION": "3.9.0", "TEST_WORKLOAD": "big5",
                  "SINGLE_NODE_CLUSTER": "true", "MIN_DISTRIBUTION": "false", "DATA_INSTANCE_TYPE": "c5.2xlarge",
                  "WORKLOAD_PARAMS": '{"snapshot_repo_name":"repo"}', "TEST_PROCEDURE": "restore-from-snapshot",
                  "CAPTURE_NODE_STAT": "true", "TELEMETRY_PARAMS": '{"node-stats-sample-interval":5}',
                  "ADDITIONAL_CONFIG": "a.b:true c.d:SEGMENT"}
        args = benchmark_test.test_args(params, "42-1", True, "security-enabled:false", "/w/config.yml", "/w/benchmark.ini")
        self.assertEqual(args, [
            "benchmark-test", "execute-test", "--distribution-url", "https://a/os.tar.gz", "--distribution-version", "3.9.0",
            "--config", "/w/config.yml", "--workload", "big5", "--benchmark-config", "/w/benchmark.ini",
            "--user-tag", "security-enabled:false", "--without-security", "--single-node", "--capture-node-stat",
            "--suffix", "42-1", "--data-instance-type", "c5.2xlarge", "--workload-params", '{"snapshot_repo_name":"repo"}',
            "--test-procedure", "restore-from-snapshot", "--additional-config", "a.b:true", "c.d:SEGMENT",
            "--telemetry-params", '{"node-stats-sample-interval":5}'])

    def test_endpoint_args(self) -> None:
        params = {"CLUSTER_ENDPOINT": "vpc-x", "SIGV4": "true", "REGION": "us-east-1", "SERVICE": "es", "TEST_WORKLOAD": "big5",
                  "INCLUDE_TASKS": "type:search"}
        args = benchmark_test.test_args(params, "42-1", False, "security-enabled:true", benchmark_config="/w/benchmark.ini")
        self.assertIn("--cluster-endpoint", args)
        self.assertNotIn("--config", args)
        self.assertEqual(args[args.index("--sigv4"):args.index("--sigv4") + 5], ["--sigv4", "--region", "us-east-1", "--service", "es"])

    def test_bundle_manifest_and_local_endpoint(self) -> None:
        args = benchmark_test.test_args({"TEST_MODE": "true"}, "1", True, "", bundle_manifest="bundle-manifest.yml", endpoint="10.0.0.1")
        self.assertEqual(args[:6], ["benchmark-test", "execute-test", "--bundle-manifest", "bundle-manifest.yml", "--cluster-endpoint", "10.0.0.1"])
        self.assertEqual(args[-1], "--test-mode")

    def test_compare_args(self) -> None:
        self.assertEqual(benchmark_test.compare_args("b-1", "c-2", "9", "/w/benchmark.ini"),
                         ["benchmark-test", "compare", "b-1", "c-2", "--benchmark-config", "/w/benchmark.ini", "--suffix", "9"])
        with self.assertRaises(ValueError):
            benchmark_test.compare_args("b 1", "c", "9")

    def test_run_masks_the_password(self) -> None:
        with patch.dict(os.environ, {"BENCHMARK_CLUSTER_USERNAME": "admin", "BENCHMARK_CLUSTER_PASSWORD": "s3cret"}), \
                patch("builtins.print") as mock_print, patch("subprocess.call", return_value=0) as mock_call:
            self.assertEqual(benchmark_test.main(["run", "--params", '{"CLUSTER_ENDPOINT": "e"}', "--suffix", "1"]), 0)
        shown = mock_print.call_args_list[0][0][0]
        self.assertNotIn("s3cret", shown)
        self.assertEqual(mock_call.call_args[0][0][-2:], ["--password", "s3cret"])


class TestOutputs(unittest.TestCase):
    def test_render_comment(self) -> None:
        body = benchmark_test.render_comment("Benchmark Results", "https://run/1", "| Metric | Value |\n")
        self.assertEqual(body, "<details><summary>Benchmark Results</summary>\n\n#### Benchmark Results for Job: https://run/1\n"
                               "| Metric | Value |\n</details>\n")
        self.assertEqual(benchmark_test.failure_comment("https://run/1"), "The benchmark job https://run/1 failed.\n Please see logs to debug.")

    def test_comment_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results, output = os.path.join(tmp, "final.md"), os.path.join(tmp, "comment.md")
            with open(results, "w") as f:
                f.write("table\n")
            with patch("builtins.print"):
                benchmark_test.main(["comment", "--title", "Benchmark Results", "--run-url", "u", "--results", results, "--output", output])
            with open(output) as f:
                self.assertTrue(f.read().startswith("<details><summary>Benchmark Results</summary>"))

    def test_compare_queries_and_ids(self) -> None:
        queries = benchmark_test.compare_queries("cfg", "3.9.0-SNAPSHOT", "nyc_taxis", "12345")
        self.assertEqual(queries["baseline"]["query"]["bool"]["must"][0], {"term": {"user-tags.cluster-config": "cfg"}})
        self.assertEqual(queries["contender"]["query"]["bool"]["must"][0], {"term": {"user-tags.pull_request_number": "12345"}})
        self.assertEqual(benchmark_test.latest_execution_id({"hits": {"total": {"value": 1}, "hits": [{"_source": {"test-execution-id": "id-1"}}]}}), "id-1")
        self.assertEqual(benchmark_test.latest_execution_id({"hits": {"total": {"value": 0}, "hits": []}}), "")

    def test_edit_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "benchmark.ini")
            with open(path, "w") as f:
                f.write("datastore.user = insert_user_here\ndatastore.password = insert_password_here\n")
            benchmark_test.edit_config(path, "u", "p")
            with open(path) as f:
                self.assertEqual(f.read(), "datastore.user = u\ndatastore.password = p\n")

    def test_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = os.path.join(tmp, "env")
            with patch.dict(os.environ, {"GITHUB_ENV": env_file, "BENCHMARK_ENDPOINT_CREDENTIALS": '{"aos": "admin:pa:ss"}'}), \
                    patch("builtins.print"):
                self.assertEqual(benchmark_test.main(["credentials", "--key", "aos"]), 0)
                self.assertEqual(benchmark_test.main(["credentials", "--key", "missing"]), 1)
            with open(env_file) as f:
                self.assertEqual(f.read(), "BENCHMARK_CLUSTER_USERNAME=admin\nBENCHMARK_CLUSTER_PASSWORD=pa:ss\n")

    def test_stack_names(self) -> None:
        self.assertEqual(benchmark_test.stack_names("42-1"), ["opensearch-infra-stack-42-1"])
        self.assertEqual(benchmark_test.stack_names("42-1-secure", "11111", "x64"), ["opensearch-infra-stack-42-1-secure-11111-x64"])

    def test_webhook_text(self) -> None:
        with patch.dict(os.environ, {"WEBHOOK_URL": ""}), patch("builtins.print") as mock_print:
            benchmark_test.main(["webhook", "--icon", ":warning:", "--job-name", "benchmark-test", "--run-number", "7",
                                 "--message", "Benchmark Tests Failed", "--run-url", "u", "--extra", "Benchmark tests for x"])
        payload = json.loads(mock_print.call_args_list[0][0][0])
        self.assertEqual(payload["result_text"], ":warning:\nJOB_NAME=benchmark-test\nBUILD_NUMBER=[7]\nMESSAGE=Benchmark Tests Failed\n"
                                                 "BUILD_URL: u\nBenchmark tests for x")
