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
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts")
SPEC = importlib.util.spec_from_file_location("integ_test_failures", os.path.join(SCRIPTS, "integ_test_failures.py"))
integ: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(integ)

MANIFEST = os.path.join(os.path.dirname(__file__), "..", "..", "manifests", "3.9.0", "opensearch-3.9.0.yml")


def hit(**source: Any) -> Dict[str, Any]:
    return {"_source": source}


def bucket(result: str, distribution: str = "tar", architecture: str = "x64") -> Dict[str, Any]:
    source = {"platform": "linux", "distribution": distribution, "architecture": architecture, "component_build_result": result,
              "test_report_manifest_yml": f"https://ci/{distribution}/test-report.yml", "integ_test_build_url": "https://ci/job/1", "rc_number": 0}
    return {"latest_doc": {"hits": {"hits": [hit(**source)]}}}


class FakeMetrics:
    def __init__(self, failed_buckets: Dict[str, List[Dict[str, Any]]], owners: Dict[str, Any] = None) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self.failed_buckets = failed_buckets
        self.owners = owners or {}

    def __call__(self, index: str, query: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append((index, query))
        filters = {k: v for f in query.get("query", {}).get("bool", {}).get("filter", []) for k, v in f.get("match_phrase", {}).items()}
        if index == integ.BUILD_INDEX:
            return {"hits": {"hits": [hit(distribution_build_number=12228)]}}
        if index == integ.RELEASE_INDEX:
            component = filters["component.keyword"]
            if component == "broken":
                raise RuntimeError("boom")
            return {"hits": {"hits": [hit(release_owners=self.owners.get(component, []))]}}
        if "component_build_result" in filters:
            names = {"failed": ["sql", "k-NN", "sql"], "passed": ["sql", "security", "k-NN", "job-scheduler"]}[filters["component_build_result"]]
            return {"hits": {"total": {"value": len(names)}, "hits": [hit(component=n) for n in names]}}
        buckets = self.failed_buckets.get(filters["component"], [])
        return {"hits": {"total": {"value": len(buckets)}}, "aggregations": {"unique_combinations": {"buckets": buckets}}}


class TestQueries(unittest.TestCase):
    def test_qualifier_filter(self) -> None:
        for value in (None, "", " ", "None", "Null"):
            self.assertEqual(integ.qualifier_filter(value), [])
        self.assertEqual(integ.qualifier_filter("alpha1"), [{"match_phrase": {"qualifier": "alpha1"}}])

    def test_components_query_matches_jenkins(self) -> None:
        self.assertEqual(integ.components_query("OpenSearch", "3.9.0", "None", "12228", "failed"), {
            "size": 50,
            "_source": ["component"],
            "query": {"bool": {"filter": [
                {"match_phrase": {"version": "3.9.0"}},
                {"match_phrase": {"component_category": "OpenSearch"}},
                {"match_phrase": {"distribution_build_number": "12228"}},
                {"match_phrase": {"component_build_result": "failed"}},
            ]}},
        })

    def test_top_results_query_keeps_latest_run_per_combination(self) -> None:
        query = integ.top_results_query("sql", "3.9.0", "beta1", "12228")
        self.assertEqual(query["query"]["bool"]["filter"][-1], {"match_phrase": {"qualifier": "beta1"}})
        latest = query["aggs"]["unique_combinations"]["aggs"]["latest_doc"]["top_hits"]
        self.assertEqual(latest["sort"], [{"integ_test_build_number": {"order": "desc"}}])
        self.assertEqual(latest["size"], 1)


class TestCollect(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = {"build": {"name": "OpenSearch", "version": "3.9.0"}}

    def test_collect_failures_passed_and_flaky(self) -> None:
        search = FakeMetrics({"sql": [bucket("failed"), bucket("passed", "rpm"), bucket("failed", "deb", "arm64")], "k-NN": [bucket("passed")]}, {"sql": ["alice", "bob"]})
        with patch("builtins.print"):
            result = integ.collect(search, self.manifest)
        self.assertEqual(result["distribution_build_number"], "12228")
        self.assertEqual([(r["component"], r["distribution"], r["architecture"]) for r in result["failures"]], [("sql", "tar", "x64"), ("sql", "deb", "arm64")])
        self.assertEqual(result["failures"][0]["distribution_build_number"], "12228")
        self.assertEqual(result["flaky_skipped"], ["k-NN"])
        self.assertEqual(result["passed"], ["security", "job-scheduler"])
        self.assertEqual(result["release_owners"], {"sql": ["alice", "bob"]})
        self.assertFalse(any(index == integ.RELEASE_INDEX and "k-NN" in json.dumps(q) for index, q in search.calls))

    def test_given_build_number_skips_lookup(self) -> None:
        search = FakeMetrics({})
        with patch("builtins.print"):
            result = integ.collect(search, dict(self.manifest, build={"name": "OpenSearch", "version": "3.9.0", "qualifier": "alpha1"}), "999")
        self.assertEqual(result["distribution_build_number"], "999")
        self.assertNotIn(integ.BUILD_INDEX, [index for index, _ in search.calls])
        self.assertIn({"match_phrase": {"qualifier": "alpha1"}}, search.calls[0][1]["query"]["bool"]["filter"])

    def test_release_owner_errors_are_not_fatal(self) -> None:
        search = FakeMetrics({"broken": [bucket("failed")]})
        with patch("builtins.print"):
            self.assertEqual(integ.release_owners(search, "broken", "3.9.0"), [])

    def test_main_writes_step_outputs(self) -> None:
        search = FakeMetrics({"sql": [bucket("failed")]}, {"sql": ["alice"]})
        with tempfile.TemporaryDirectory() as tmp:
            output, summary, report = (os.path.join(tmp, name) for name in ("out", "summary", "report.json"))
            with patch.dict(os.environ, {"GITHUB_OUTPUT": output, "GITHUB_STEP_SUMMARY": summary}), patch("builtins.print"):
                self.assertEqual(integ.main(["--input-manifest", MANIFEST, "--distribution-number", "12228", "--report", report], search=search), 0)
            with open(output) as f:
                text = f.read()
            with open(summary) as f:
                summary_text = f.read()
            with open(report) as f:
                saved = json.load(f)
        self.assertIn("distribution-build-number<<EOF_DISTRIBUTION_BUILD_NUMBER_", text)
        self.assertIn('"component": "sql"', text)
        self.assertIn("security\njob-scheduler\n", text)
        self.assertIn('{"sql": ["alice"]}', text)
        self.assertIn("| sql | failed | linux-tar-x64 |", summary_text)
        self.assertIn("| k-NN | flaky (latest run passed), skipped | |", summary_text)
        self.assertEqual(saved["version"], "3.9.0")
