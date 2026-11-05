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
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts")
SPEC = importlib.util.spec_from_file_location("gradle_check_flaky_tests", os.path.join(SCRIPTS, "gradle_check_flaky_tests.py"))
flaky: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(flaky)
CLUSTER_SPEC = importlib.util.spec_from_file_location("metrics_cluster", os.path.join(SCRIPTS, "metrics_cluster.py"))
metrics_cluster: Any = importlib.util.module_from_spec(CLUSTER_SPEC)
CLUSTER_SPEC.loader.exec_module(metrics_cluster)


def aggs(**buckets: List[Any]) -> Dict[str, Any]:
    return {"aggregations": {name: {"buckets": [{"key": key} for key in keys]} for name, keys in buckets.items()}}


def fake_search(index: str, query: Dict[str, Any]) -> Dict[str, Any]:
    assert index == "gradle-check"
    names = query["aggregations"].keys()
    if "test_class_keyword_agg" in names:
        return aggs(test_class_keyword_agg=["FooIT"])
    if "git_reference_keyword_agg" in names:
        return aggs(git_reference_keyword_agg=["abc123", "2.x", "def456"])
    if "test_name_keyword_agg" in names:
        ref = query["query"]["bool"]["must"][3]["match"]["git_reference.keyword"]["query"]
        prs = {"abc123": [101], "2.x": [102], "def456": [103, 104]}[ref]
        return aggs(test_name_keyword_agg=["testA", "testB"], build_number_agg=[9, 10], pull_request_agg=prs)
    return aggs(pull_request_keyword_agg=[101, 200, 201])


class TestQueries(unittest.TestCase):
    def test_failed_test_class_query(self) -> None:
        query = flaky.failed_test_class_query("30d")
        self.assertEqual(query["size"], 200)
        must = query["query"]["bool"]["must"]
        self.assertEqual(must[0], {"terms": {"invoke_type.keyword": ["Post Merge Action", "Timer"]}})
        self.assertEqual(must[1]["match"]["test_status"]["query"], "FAILED")
        self.assertEqual(query["query"]["bool"]["filter"][0]["range"]["build_start_time"]["from"], "now-30d")
        self.assertEqual(query["aggregations"], {"test_class_keyword_agg": {"terms": {"field": "test_class", "size": 500}}})

    def test_git_reference_query_orders_by_latest_build(self) -> None:
        agg = flaky.git_reference_query("FooIT")["aggregations"]["git_reference_keyword_agg"]
        self.assertEqual(agg["terms"]["order"], {"sort_build_number": "desc"})
        self.assertNotIn("filter", flaky.git_reference_query("FooIT")["query"]["bool"])

    def test_pull_request_query(self) -> None:
        must = flaky.test_pull_requests_query("FooIT")["query"]["bool"]["must"]
        self.assertEqual(must[0]["match"]["invoke_type.keyword"]["query"], "Pull Request")


class TestReport(unittest.TestCase):
    def test_markdown_matches_create_markdown_table(self) -> None:
        body = flaky.report_for(fake_search, "FooIT")
        build_links = "[9](https://build.ci.opensearch.org/job/gradle-check/9/testReport/) <br><br>[10](https://build.ci.opensearch.org/job/gradle-check/10/testReport/)"
        self.assertEqual(body, (
            "   \n## Flaky Test Report for `FooIT`\n\n"
            "Noticed the `FooIT` has some flaky, failing tests that failed during **post-merge actions** and **timer trigerred run on main branch**.\n\n"
            "### Details\n\n"
            "| Git Reference | Merged Pull Request | Build Details | Test Name |\n"
            "|---------------|----------------------|---------------|-----------|\n"
            f"| abc123 | [101](https://github.com/opensearch-project/OpenSearch/pull/101) | {build_links} | `testA`<br><br>`testB` |\n"
            "| def456 | [103](https://github.com/opensearch-project/OpenSearch/pull/103)<br><br>[104](https://github.com/opensearch-project/OpenSearch/pull/104)"
            f" | {build_links} | `testA`<br><br>`testB` |"
            "\n\nThe other pull requests, besides those involved in post-merge actions, that contain failing tests with the `FooIT` class are:\n\n"
            "- [200](https://github.com/opensearch-project/OpenSearch/pull/200)\n- [201](https://github.com/opensearch-project/OpenSearch/pull/201)"
            "\n\nFor more details on the failed tests refer to [OpenSearch Gradle Check Metrics]"
            "(https://metrics.opensearch.org/_dashboards/app/dashboards#/view/e5e64d40-ed31-11ee-be99-69d1dbc75083) dashboard.\n"
        ))

    def test_failed_lookups_render_empty(self) -> None:
        def failing(index: str, query: Dict[str, Any]) -> Dict[str, Any]:
            raise RuntimeError("boom")
        with patch("builtins.print"):
            self.assertEqual(flaky.detect(failing, "30d"), [])
            body = flaky.report_for(failing, "FooIT")
        self.assertIn("|-----------|\n\n\nThe other pull requests", body)

    def test_main_writes_issues_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "issues.json")
            with patch.dict(os.environ, {"GITHUB_OUTPUT": os.path.join(tmp, "out")}), patch("builtins.print"):
                self.assertEqual(flaky.main(["--issues-file", path], search=fake_search), 0)
            with open(path) as f:
                issues = json.load(f)
        self.assertEqual([(i["test_class"], i["title"]) for i in issues], [("FooIT", "[AUTOCUT] Gradle Check Flaky Test Report for FooIT")])


class TestMetricsCluster(unittest.TestCase):
    def test_public_proxy_request(self) -> None:
        session = MagicMock()
        session.post.return_value.json.return_value = {"hits": {}}
        cluster = metrics_cluster.MetricsCluster(dashboards_url="https://metrics.example/_dashboards/", session=session)
        self.assertEqual(cluster.search("gradle-check", {"size": 0}), {"hits": {}})
        session.post.assert_called_once_with(
            "https://metrics.example/_dashboards/api/console/proxy", params={"path": "gradle-check/_search", "method": "GET"},
            headers={"osd-xsrf": "true"}, json={"size": 0}, timeout=60)

    def test_signed_request_needs_credentials(self) -> None:
        with patch.dict(os.environ, {}, clear=True), pytest.raises(RuntimeError):
            metrics_cluster.MetricsCluster(endpoint="https://cluster.example")
        with pytest.raises(ValueError):
            metrics_cluster.MetricsCluster()

    def test_signed_request(self) -> None:
        session = MagicMock()
        session.get.return_value.json.return_value = {"ok": True}
        with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "a", "AWS_SECRET_ACCESS_KEY": "s", "AWS_SESSION_TOKEN": "t"}):
            cluster = metrics_cluster.MetricsCluster(endpoint="https://cluster.example/", session=session)
        self.assertEqual(cluster.search("idx", {}), {"ok": True})
        args, kwargs = session.get.call_args
        self.assertEqual(args, ("https://cluster.example/idx/_search",))
        self.assertIsNotNone(kwargs["auth"])
        self.assertEqual(cluster.source, "metrics cluster (SigV4)")
