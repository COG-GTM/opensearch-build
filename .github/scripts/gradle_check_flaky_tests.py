#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Detect Gradle Check flaky tests and write one Flaky Test Report issue body per test class.

Query half of opensearch-build-libraries (jenkins@12.0.0) vars/gradleCheckFlakyTestDetector.groovy with
src/gradlecheck/FetchPostMergeFailedTestClass, FetchPostMergeTestGitReference, FetchPostMergeFailedTestName,
FetchTestPullRequests and CreateMarkDownTable: same `gradle-check` queries, the issue body byte-for-byte.
The issue writes (gradleCheckFlakyTestGitHubIssue) are done by .github/actions/notify-failure (kind: flaky-test)
from the JSON list written to --issues-file: [{"test_class", "title", "body"}, ...].
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metrics_cluster import Search, from_args, write_output, write_summary  # noqa: E402 isort:skip

INDEX = "gradle-check"
POST_MERGE_INVOKE_TYPES = ["Post Merge Action", "Timer"]
EXCLUDED_GIT_REFERENCES = ("2.19", "2.x", "1.x")
OPENSEARCH_PR_URL = "https://github.com/opensearch-project/OpenSearch/pull/{}"
BUILD_URL = "https://build.ci.opensearch.org/job/gradle-check/{}/testReport/"
DASHBOARD_URL = "https://metrics.opensearch.org/_dashboards/app/dashboards#/view/e5e64d40-ed31-11ee-be99-69d1dbc75083"


def match(field: str, value: str) -> Dict[str, Any]:
    """The Groovy queries spell out every match option; they are the OpenSearch defaults."""
    return {"match": {field: {
        "query": value, "operator": "OR", "prefix_length": 0, "max_expansions": 50, "fuzzy_transpositions": True,
        "lenient": False, "zero_terms_query": "NONE", "auto_generate_synonyms_phrase_query": True, "boost": 1,
    }}}


def bool_query(must: List[Dict[str, Any]], filters: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    query: Dict[str, Any] = {"must": must}
    if filters:
        query["filter"] = filters
    query.update({"adjust_pure_negative": True, "boost": 1})
    return {"bool": query}


def post_merge_failed(*extra: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"terms": {"invoke_type.keyword": POST_MERGE_INVOKE_TYPES}}, match("test_status", "FAILED"), *extra]


def failed_test_class_query(time_frame: str) -> Dict[str, Any]:
    time_range = {"range": {"build_start_time": {"from": f"now-{time_frame}", "to": "now", "include_lower": True, "include_upper": True, "boost": 1}}}
    return {
        "size": 200,
        "query": bool_query(post_merge_failed(), [time_range]),
        "aggregations": {"test_class_keyword_agg": {"terms": {"field": "test_class", "size": 500}}},
    }


def git_reference_query(test_class: str) -> Dict[str, Any]:
    return {
        "size": 200,
        "query": bool_query(post_merge_failed(match("test_class", test_class))),
        "aggregations": {"git_reference_keyword_agg": {
            "terms": {"field": "git_reference.keyword", "size": 500, "order": {"sort_build_number": "desc"}},
            "aggs": {"sort_build_number": {"max": {"field": "build_number"}}},
        }},
    }


def failed_test_name_query(test_class: str, git_reference: str) -> Dict[str, Any]:
    return {
        "size": 200,
        "query": bool_query(post_merge_failed(match("test_class", test_class), match("git_reference.keyword", git_reference))),
        "aggregations": {
            "test_name_keyword_agg": {"terms": {"field": "test_name", "size": 500}},
            "build_number_agg": {"terms": {"field": "build_number", "size": 500}},
            "pull_request_agg": {"terms": {"field": "pull_request", "size": 500}},
        },
    }


def test_pull_requests_query(test_class: str) -> Dict[str, Any]:
    return {
        "size": 200,
        "query": bool_query([match("invoke_type.keyword", "Pull Request"), match("test_status", "FAILED"), match("test_class", test_class)]),
        "aggregations": {"pull_request_keyword_agg": {"terms": {"field": "pull_request", "size": 500}}},
    }


def bucket_keys(response: Dict[str, Any], aggregation: str) -> List[Any]:
    return [bucket["key"] for bucket in response["aggregations"][aggregation]["buckets"]]


def safe_keys(search: Search, query: Dict[str, Any], aggregation: str, what: str) -> List[Any]:
    """The Groovy fetchers print the error and return null, which the caller iterates as empty."""
    try:
        return bucket_keys(search(INDEX, query), aggregation)
    except Exception as ex:
        print(f"Error fetching {what}: {ex}", file=sys.stderr)
        return []


def create_markdown_table(failed_test: str, test_data: List[Dict[str, Any]], additional_pull_requests: List[Any]) -> str:
    """CreateMarkDownTable.createMarkdownTable()."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in test_data:
        if row["gitReference"] and row["gitReference"] not in EXCLUDED_GIT_REFERENCES:
            groups.setdefault(row["gitReference"], []).append(row)
    table_rows = []
    for ref, rows in groups.items():
        pull_requests = "<br><br>".join(dict.fromkeys(r["pullRequestLink"] for r in rows))
        builds = "<br><br>".join(dict.fromkeys(r["buildDetailLink"] for r in rows))
        names = "<br><br>".join(dict.fromkeys(name for r in rows for name in r["testNames"]))
        table_rows.append(f"| {ref} | {pull_requests} | {builds} | {names} |")
    header = (
        "   \n"
        f"## Flaky Test Report for `{failed_test}`\n\n"
        f"Noticed the `{failed_test}` has some flaky, failing tests that failed during **post-merge actions** and **timer trigerred run on main branch**.\n\n"
        "### Details\n\n"
        "| Git Reference | Merged Pull Request | Build Details | Test Name |\n"
        "|---------------|----------------------|---------------|-----------|\n"
    )
    additional = (
        "\n\nThe other pull requests, besides those involved in post-merge actions, that contain failing tests with the "
        f"`{failed_test}` class are:\n\n"
        + "\n".join(f"- [{pr}]({OPENSEARCH_PR_URL.format(pr)})" for pr in additional_pull_requests)
        + f"\n\nFor more details on the failed tests refer to [OpenSearch Gradle Check Metrics]({DASHBOARD_URL}) dashboard.\n"
    )
    return header + "\n".join(table_rows) + additional


def report_for(search: Search, failed_test: str) -> str:
    test_data = []
    all_pull_requests: List[Any] = []
    for git_reference in safe_keys(search, git_reference_query(failed_test), "git_reference_keyword_agg", "Git Reference Details"):
        response = search(INDEX, failed_test_name_query(failed_test, git_reference))
        test_names = bucket_keys(response, "test_name_keyword_agg")
        build_numbers = bucket_keys(response, "build_number_agg")
        pull_requests = bucket_keys(response, "pull_request_agg")
        all_pull_requests.extend(pull_requests)
        test_data.append({
            "gitReference": git_reference,
            "pullRequestLink": "<br><br>".join(f"[{pr}]({OPENSEARCH_PR_URL.format(pr)})" for pr in pull_requests),
            "buildDetailLink": " <br><br>".join(f"[{build}]({BUILD_URL.format(build)})" for build in build_numbers),
            "testNames": [f"`{name}`" for name in test_names],
        })
    additional = [pr for pr in safe_keys(search, test_pull_requests_query(failed_test), "pull_request_keyword_agg", "Pull Request Details") if pr not in all_pull_requests]
    return create_markdown_table(failed_test, test_data, additional)


def detect(search: Search, time_frame: str) -> List[Dict[str, str]]:
    classes = safe_keys(search, failed_test_class_query(time_frame), "test_class_keyword_agg", "Failed Test Class Details")
    print(f"{len(classes)} test classes failed post-merge in the last {time_frame}")
    return [
        {"test_class": test_class, "title": f"[AUTOCUT] Gradle Check Flaky Test Report for {test_class}", "body": report_for(search, test_class)}
        for test_class in classes
    ]


def main(argv: Optional[List[str]] = None, search: Optional[Search] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--time-frame", default="30d", help="OpenSearch date math range, e.g. 30d")
    parser.add_argument("--issues-file", required=True, help="Write the issue list (JSON) here")
    parser.add_argument("--endpoint", default=os.environ.get("METRICS_CLUSTER_ENDPOINT", ""), help="Metrics cluster URL (SigV4)")
    parser.add_argument("--dashboards-url", default="", help="Read through this Dashboards console proxy instead (dry runs)")
    args = parser.parse_args(argv)

    issues = detect(search or from_args(args.endpoint, args.dashboards_url).search, args.time_frame or "30d")
    with open(args.issues_file, "w") as issues_file:
        json.dump(issues, issues_file, indent=2)
    write_output("count", str(len(issues)))
    write_summary(f"### Gradle Check flaky tests (last {args.time_frame})\n\n" + "\n".join(f"- `{issue['test_class']}`" for issue in issues))
    return 0


if __name__ == "__main__":
    sys.exit(main())
