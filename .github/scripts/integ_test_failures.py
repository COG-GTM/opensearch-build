#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Collect the integration test results of a distribution build for the notify-failure action.

Query half of opensearch-build-libraries (jenkins@12.0.0) vars/updateIntegTestFailureIssues.groovy with
src/jenkins/ComponentIntegTestStatus.groovy, ComponentBuildStatus.getLatestDistributionBuildNumber() and
ReleaseMetricsData.getReleaseOwners(): same indices, filters and sizes. The issue writes are done by
.github/actions/notify-failure (kind: integ-test) from the step outputs written here:

  distribution-build-number  the build that was queried (latest for the version when not given)
  failures                   JSON list of failed table rows, each with its component
  passed                     components whose AUTOCUT issue is closed (passed and not failed)
  release-owners             JSON object {component: [login, ...]}
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metrics_cluster import Search, from_args, write_output, write_summary  # noqa: E402 isort:skip

INTEG_TEST_INDEX = "opensearch-integration-test-results"
BUILD_INDEX = "opensearch-distribution-build-results"
RELEASE_INDEX = "opensearch_release_metrics"
COMPONENTS_QUERY_SIZE = 50
# distribution_build_number is an integer field of the metrics indices; GitHub run ids (the GHA build.id) exceed it.
MAX_BUILD_NUMBER = 2**31 - 1
BUILD_NUMBER_FOLLOW_UP = "Make GHA build numbers fit Jenkins integer fields"


def qualifier_filter(qualifier: Optional[str]) -> List[Dict[str, Any]]:
    """isNullOrEmpty(): 'None', 'Null', null and blank qualifiers add no filter."""
    if qualifier is None or not str(qualifier).strip() or str(qualifier) in ("None", "Null"):
        return []
    return [{"match_phrase": {"qualifier": str(qualifier)}}]


def latest_build_query(product: str, version: str, qualifier: Optional[str]) -> Dict[str, Any]:
    return {
        "size": 1,
        "_source": ["distribution_build_number"],
        "query": {"bool": {"filter": [
            {"match_phrase": {"component_category": product}},
            {"match_phrase": {"version": version}},
        ] + qualifier_filter(qualifier)}},
        "sort": [{"build_start_time": {"order": "desc"}}],
    }


def components_query(product: str, version: str, qualifier: Optional[str], build_number: str, result: str) -> Dict[str, Any]:
    return {
        "size": COMPONENTS_QUERY_SIZE,
        "_source": ["component"],
        "query": {"bool": {"filter": [
            {"match_phrase": {"version": version}},
            {"match_phrase": {"component_category": product}},
            {"match_phrase": {"distribution_build_number": build_number}},
            {"match_phrase": {"component_build_result": result}},
        ] + qualifier_filter(qualifier)}},
    }


def top_results_query(component: str, version: str, qualifier: Optional[str], build_number: str) -> Dict[str, Any]:
    return {
        "size": 0,
        "query": {"bool": {"filter": [
            {"match_phrase": {"component": component}},
            {"match_phrase": {"version": version}},
            {"match_phrase": {"distribution_build_number": build_number}},
        ] + qualifier_filter(qualifier)}},
        "aggs": {"unique_combinations": {
            "composite": {"size": 100, "sources": [
                {"platform": {"terms": {"field": "platform"}}},
                {"architecture": {"terms": {"field": "architecture"}}},
                {"distribution": {"terms": {"field": "distribution"}}},
            ]},
            "aggs": {"latest_doc": {"top_hits": {
                "size": 1,
                "sort": [{"integ_test_build_number": {"order": "desc"}}],
                "_source": ["platform", "architecture", "distribution", "test_report_manifest_yml", "integ_test_build_url", "rc_number", "component_build_result"],
            }}},
        }},
    }


def release_owners_query(component: str, version: str) -> Dict[str, Any]:
    return {
        "size": 1,
        "_source": ["release_owners", "release_issue_exists"],
        "query": {"bool": {"filter": [
            {"match_phrase": {"version": version}},
            {"match_phrase": {"component.keyword": component}},
        ]}},
        "sort": [{"current_date": {"order": "desc"}}],
    }


def unique(values: List[str]) -> List[str]:
    return list(dict.fromkeys(values))


def latest_build_number(search: Search, product: str, version: str, qualifier: Optional[str]) -> str:
    hits = search(BUILD_INDEX, latest_build_query(product, version, qualifier))["hits"]["hits"]
    if not hits:
        raise RuntimeError(f"No {product} {version} distribution build in {BUILD_INDEX}")
    return str(hits[0]["_source"]["distribution_build_number"])


def components(search: Search, product: str, version: str, qualifier: Optional[str], build_number: str, result: str) -> List[str]:
    response = search(INTEG_TEST_INDEX, components_query(product, version, qualifier, build_number, result))
    total = response["hits"].get("total", {}).get("value", 0)
    if total > COMPONENTS_QUERY_SIZE:
        print(f"Warning: {total} '{result}' results, only the first {COMPONENTS_QUERY_SIZE} are read (same size as Jenkins)", file=sys.stderr)
    return unique([hit["_source"]["component"] for hit in response["hits"]["hits"]])


def failed_rows(search: Search, component: str, version: str, qualifier: Optional[str], build_number: str) -> List[Dict[str, Any]]:
    """getComponentIntegTestFailedData(): latest run per platform/architecture/distribution, kept when it failed."""
    try:
        response = search(INTEG_TEST_INDEX, top_results_query(component, version, qualifier, build_number))
    except Exception as ex:
        print(f"Error getting component integration test failed data for {component}: {ex}", file=sys.stderr)
        return []
    if response["hits"]["total"]["value"] == 0:
        print(f"No integration test failed data found for component: {component}", file=sys.stderr)
        return []
    rows = []
    for bucket in response["aggregations"]["unique_combinations"]["buckets"]:
        hit = bucket["latest_doc"]["hits"]["hits"][0]["_source"]
        if hit.get("component_build_result") == "failed":
            rows.append({
                "component": component,
                "platform": hit.get("platform"),
                "distribution": hit.get("distribution"),
                "architecture": hit.get("architecture"),
                "test_report_manifest_yml": hit.get("test_report_manifest_yml"),
                "integ_test_build_url": hit.get("integ_test_build_url"),
                "distribution_build_number": build_number,
                "rc_number": hit.get("rc_number"),
            })
    return rows


def release_owners(search: Search, component: str, version: str) -> List[str]:
    try:
        hits = search(RELEASE_INDEX, release_owners_query(component, version))["hits"]["hits"]
    except Exception as ex:
        print(f"Error fetching release owners: {ex}", file=sys.stderr)
        return []
    owners: List[str] = []
    for hit in hits:
        value = hit["_source"].get("release_owners") or []
        owners.extend(value if isinstance(value, list) else [value])
    return owners


def collect(search: Search, manifest: Dict[str, Any], build_number: str = "") -> Dict[str, Any]:
    build = manifest["build"]
    version = str(build["version"])
    qualifier = build.get("qualifier") or "None"
    product = build["name"]
    build_number = build_number or latest_build_number(search, product, version, qualifier)
    print(f"Distribution Build Number: {build_number}")
    passed = components(search, product, version, qualifier, build_number, "passed")
    failed = components(search, product, version, qualifier, build_number, "failed")
    print(f"Failed Components: {failed}")
    print(f"Passed Components: {passed}")

    failures: List[Dict[str, Any]] = []
    owners: Dict[str, List[str]] = {}
    skipped: List[str] = []
    for component in failed:
        rows = failed_rows(search, component, version, qualifier, build_number)
        if not rows:
            print(f"Warning: Latest test run passed for {component} but found previous failures indicating flakiness. Skipping issue update.")
            skipped.append(component)
            continue
        failures.extend(rows)
        owners[component] = release_owners(search, component, version)
    return {
        "product": product,
        "version": version,
        "distribution_build_number": build_number,
        "failures": failures,
        "passed": [component for component in passed if component not in failed],
        "flaky_skipped": skipped,
        "release_owners": owners,
    }


def summary(result: Dict[str, Any], input_manifest: str) -> str:
    failed = unique([row["component"] for row in result["failures"]])
    lines = [
        f"### Integration test results for {input_manifest}",
        "",
        f"{result['product']} {result['version']}, distribution build {result['distribution_build_number']}",
        "",
        "| Component | Result | Failed platforms |",
        "|-----------|--------|------------------|",
    ]
    for component in failed:
        platforms = sorted({f"{r['platform']}-{r['distribution']}-{r['architecture']}" for r in result["failures"] if r["component"] == component})
        lines.append(f"| {component} | failed | {', '.join(platforms)} |")
    lines += [f"| {component} | flaky (latest run passed), skipped | |" for component in result["flaky_skipped"]]
    lines += [f"| {component} | passed | |" for component in result["passed"]]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None, search: Optional[Search] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-manifest", required=True, help="Path to the input manifest, e.g. manifests/3.9.0/opensearch-3.9.0.yml")
    parser.add_argument("--distribution-number", default="", help="Distribution build number; defaults to the latest build of the version")
    parser.add_argument("--endpoint", default=os.environ.get("METRICS_CLUSTER_ENDPOINT", ""), help="Metrics cluster URL (SigV4)")
    parser.add_argument("--dashboards-url", default="", help="Read through this Dashboards console proxy instead (dry runs)")
    parser.add_argument("--report", default="", help="Write the collected result as JSON to this file")
    args = parser.parse_args(argv)

    build_number = args.distribution_number.strip()
    if build_number.isdigit() and int(build_number) > MAX_BUILD_NUMBER:
        print(f"::warning::Distribution build number {build_number} does not fit the integer distribution_build_number field of the "
              f"metrics indices; skipping the integration test results query and the AUTOCUT issue update until "
              f"'{BUILD_NUMBER_FOLLOW_UP}' lands.")
        write_output("skipped", "true")
        write_summary(f"### Integration test results for {args.input_manifest}\n\nSkipped: distribution build {build_number} "
                      f"is out of the metrics integer range ({BUILD_NUMBER_FOLLOW_UP}).")
        return 0

    with open(args.input_manifest) as manifest_file:
        manifest = yaml.safe_load(manifest_file)
    result = collect(search or from_args(args.endpoint, args.dashboards_url).search, manifest, build_number)

    write_output("distribution-build-number", result["distribution_build_number"])
    write_output("failures", json.dumps(result["failures"]))
    write_output("passed", "\n".join(result["passed"]))
    write_output("release-owners", json.dumps(result["release_owners"]))
    write_summary(summary(result, args.input_manifest))
    if args.report:
        with open(args.report, "w") as report:
            json.dump(result, report, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
