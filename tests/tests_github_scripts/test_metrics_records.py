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
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from tests.tests_github_scripts import DATA_DIR, load_script

metrics_records = load_script("metrics_records")

INPUT_MANIFEST = os.path.join(DATA_DIR, "input-manifest.yml")
BUILD_LOG = os.path.join(DATA_DIR, "build-log.txt")
TEST_REPORT = os.path.join(DATA_DIR, "integ-test-report.yml")


def parse_bulk(body: str) -> List[Dict[str, Any]]:
    lines = body.splitlines()
    assert len(lines) % 2 == 0
    return [{"action": json.loads(lines[i]), "doc": json.loads(lines[i + 1])} for i in range(0, len(lines), 2)]


class TestMetricsRecords(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _capfd(self, capfd: Any) -> None:
        self.capfd = capfd

    def test_monthly_index(self) -> None:
        self.assertRegex(metrics_records.monthly_index("alias"), r"^alias-\d{2}-\d{4}$")

    def test_repo_helpers(self) -> None:
        self.assertEqual(metrics_records.repo_name("https://github.com/opensearch-project/alerting.git"), "alerting")
        self.assertEqual(metrics_records.repo_url("https://github.com/opensearch-project/alerting.git"), "github.com/opensearch-project/alerting")
        self.assertEqual(metrics_records.repo_url("file:///tmp/repo"), "file:///tmp/repo")

    def test_extract_components_deduplicates(self) -> None:
        with open(BUILD_LOG) as log:
            text = log.read()
        self.assertEqual(metrics_records.extract_components(text, r"(?<=\bSuccessfully built\s).*"), ["OpenSearch-Dashboards", "securityDashboards"])
        self.assertEqual(metrics_records.extract_components(text, r"(?<=\bError building\s).*"), ["alertingDashboards"])

    def test_distribution_build_records(self) -> None:
        with open(BUILD_LOG) as log:
            body = metrics_records.distribution_build_records(
                "idx", metrics_records.load(INPUT_MANIFEST), log.read(), "42", "https://run", 1700000000000, 2, "OpenSearch Dashboards", "UNSTABLE"
            )
        records = parse_bulk(body)
        self.assertEqual([r["action"] for r in records], [{"index": {"_index": "idx"}}] * 3)
        by_component = {r["doc"]["component"]: r["doc"] for r in records}
        self.assertEqual(by_component["alertingDashboards"]["component_build_result"], "failed")
        self.assertEqual(by_component["securityDashboards"]["component_build_result"], "passed")
        doc = by_component["OpenSearch-Dashboards"]
        self.assertEqual(doc["component_repo"], "OpenSearch-Dashboards")
        self.assertEqual(doc["qualifier"], "alpha1")
        self.assertEqual(doc["distribution_build_number"], 42)
        self.assertTrue(doc["rc"])
        self.assertEqual(doc["rc_number"], 2)
        self.assertEqual(doc["overall_build_result"], "UNSTABLE")

    def test_distribution_build_records_without_logs(self) -> None:
        body = metrics_records.distribution_build_records("idx", metrics_records.load(INPUT_MANIFEST), "", "1", "u", 0, 0, "OpenSearch", "FAILURE")
        self.assertEqual(body, "")

    def test_integ_test_records(self) -> None:
        bodies = metrics_records.integ_test_records(
            "results", "failures", metrics_records.load(TEST_REPORT), "integ-test", "9001", "https://run", "https://build", 1700000000000
        )
        results = {r["doc"]["component"]: r["doc"] for r in parse_bulk(bodies["results"])}
        self.assertEqual(results["alerting"]["component_build_result"], "failed")
        self.assertEqual(results["alerting"]["with_security"], "fail")
        self.assertEqual(results["alerting"]["without_security"], "pass")
        self.assertEqual(results["alerting"]["qualifier"], "alpha1")
        self.assertEqual(results["alerting"]["version"], "3.9.0")
        self.assertEqual(results["alerting"]["rc_number"], 2)
        self.assertEqual(
            results["alerting"]["test_report_manifest_yml"],
            "https://ci.opensearch.org/ci/dbc/integ-test/3.9.0-alpha1/11089/linux/x64/tar/test-results/9001/integ-test/test-report.yml",
        )
        self.assertEqual(results["geospatial"]["component_build_result"], "failed")

        failures = [r["doc"] for r in parse_bulk(bodies["failures"])]
        self.assertEqual(
            [(f["component"], f["test_type"], f["test_class"], f["test_name"]) for f in failures],
            [
                ("alerting", "with-security", "org.opensearch.alerting.MonitorRunnerIT", "test execute monitor"),
                ("alerting", "with-security", "org.opensearch.alerting.AlertServiceIT", "org.opensearch.alerting.AlertServiceIT"),
                ("geospatial", "without-security", "Result Not Available", "Result Not Available"),
            ],
        )
        self.assertTrue(all(r["action"] == {"index": {"_index": "failures"}} for r in parse_bulk(bodies["failures"])))

    def test_smoke_test_records(self) -> None:
        body = metrics_records.smoke_test_records(
            "smoke", metrics_records.load(TEST_REPORT), "smoke-test", "77", "https://run", "https://build", 1700000000000
        )
        records = parse_bulk(body)
        self.assertTrue(all(r["action"] == {"index": {"_index": "smoke"}} for r in records))
        alerting = [r["doc"] for r in records if r["doc"]["component"] == "alerting"]
        self.assertEqual([d["component_test_spec_name"] for d in alerting], ["with-security", "without-security"])
        self.assertEqual(alerting[0]["component_test_spec_result"], "FAIL")
        self.assertEqual(alerting[0]["component_test_spec_cluster_stdout"], "local-cluster-logs/stdout.txt")
        self.assertEqual(alerting[0]["smoke_test_build_number"], 77)
        self.assertEqual(alerting[0]["qualifier"], "alpha1")
        self.assertEqual(
            alerting[0]["test_report_manifest_yml"],
            "https://ci.opensearch.org/ci/dbc/smoke-test/3.9.0-alpha1/11089/linux/x64/tar/test-results/77/smoke-test/test-report.yml",
        )

    def test_failed_tests_sentinels(self) -> None:
        self.assertEqual(metrics_records.failed_tests([]), [])
        self.assertEqual(metrics_records.failed_tests(["No Failed Test"]), [])
        self.assertEqual(metrics_records.failed_tests(["Test Result Files List Not Available"]), [("Report Not Available", "Report Not Available")])

    def test_read_logs_skips_missing(self) -> None:
        self.assertEqual(metrics_records.read_logs([os.path.join(DATA_DIR, "missing.log")]), "")

    def test_main_distribution_build_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "records.ndjson")
            argv = ["metrics_records.py", "distribution-build-results", "--input-manifest", INPUT_MANIFEST, "--build-log", BUILD_LOG,
                    "--build-number", "42", "--build-url", "https://run", "--build-start-time", "1", "--rc-number", "0", "--output", output]
            with patch("argparse._sys.argv", argv):
                self.assertEqual(metrics_records.main(), 0)
            with open(output) as records:
                self.assertEqual(len(parse_bulk(records.read())), 3)
        out, _ = self.capfd.readouterr()
        self.assertIn("alias=opensearch-distribution-build-results\n", out)
        self.assertRegex(out, r"index=opensearch-distribution-build-results-\d{2}-\d{4}\n")

    def test_main_integ_test_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "records.ndjson")
            failures = os.path.join(tmp, "failures.ndjson")
            argv = ["metrics_records.py", "integ-test-results", "--test-report", TEST_REPORT, "--integ-test-build-number", "9001",
                    "--integ-test-build-url", "https://run", "--distribution-build-url", "https://build", "--build-start-time", "1",
                    "--output", output, "--failures-output", failures]
            with patch("argparse._sys.argv", argv):
                self.assertEqual(metrics_records.main(), 0)
            with open(failures) as records:
                self.assertEqual(len(parse_bulk(records.read())), 3)
        out, _ = self.capfd.readouterr()
        self.assertIn("failures-alias=opensearch-integration-test-failures\n", out)

    def test_main_smoke_test_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "records.ndjson")
            argv = ["metrics_records.py", "smoke-test-results", "--test-report", TEST_REPORT, "--smoke-test-build-number", "77",
                    "--smoke-test-build-url", "https://run", "--distribution-build-url", "https://build", "--build-start-time", "1",
                    "--output", output]
            with patch("argparse._sys.argv", argv):
                self.assertEqual(metrics_records.main(), 0)
            with open(output) as records:
                self.assertGreater(len(parse_bulk(records.read())), 0)
        out, _ = self.capfd.readouterr()
        self.assertIn("alias=opensearch-smoke-test-results\n", out)
        self.assertRegex(out, r"index=opensearch-smoke-test-results-\d{2}-\d{4}\n")
