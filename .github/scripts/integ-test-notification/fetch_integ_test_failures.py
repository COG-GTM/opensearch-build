# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Collect integration test results for a distribution build from the OpenSearch metrics cluster.

This is the data-gathering half of the Jenkins shared-library function
`updateIntegTestFailureIssues` (opensearch-build-libraries). It queries the same indices with the
same filters and writes a machine readable report plus one markdown issue body per failed component.
The GitHub issue create/update/close half is done by `update_issues.sh`.
"""

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

import requests
import yaml
from requests_aws4auth import AWS4Auth

INTEG_TEST_INDEX = "opensearch-integration-test-results"
RELEASE_METRICS_INDEX = "opensearch_release_metrics"
SIGV4_REGION = "us-east-1"
SIGV4_SERVICE = "es"

METRICS_DASHBOARD_URL = "https://metrics.opensearch.org/_dashboards/app/dashboards?security_tenant=global#/view/21aad140-49f6-11ef-bbdd-39a9b324a5aa"

PANEL_IDS = {
    "tar_x64": "ddafb9c5-2d35-482a-9c61-1ba78b67f406",
    "tar_arm64": "c570bdfd-3122-4e31-a02d-2130d797d9fc",
    "deb_x64": "5743d5c4-be75-49b9-a81f-fef3f805ad99",
    "deb_arm64": "7a6ee111-1c99-4f96-9a3f-c0f248181980",
    "rpm_x64": "94f0246f-4246-4f05-ba11-b3e22836b8e7",
    "rpm_arm64": "eae6bad4-cffc-4672-a688-14155229ea63",
    "windows_x64": "a57afb35-8d97-4641-9b07-64ff614dab00",
}


class MetricsClient:
    """Minimal SigV4 signed search client against the OpenSearch metrics cluster."""

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        access_key = os.environ.get("AWS_ACCESS_KEY_ID")
        secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
        if not access_key or not secret_key:
            raise RuntimeError("AWS credentials for the metrics cluster are not present in the environment.")
        self.auth = AWS4Auth(
            access_key,
            secret_key,
            SIGV4_REGION,
            SIGV4_SERVICE,
            session_token=os.environ.get("AWS_SESSION_TOKEN"),
        )

    def search(self, index: str, query: Dict[str, Any]) -> Dict[str, Any]:
        response = requests.get(
            f"{self.url}/{index}/_search",
            auth=self.auth,
            headers={"Content-Type": "application/json"},
            data=json.dumps(query),
            timeout=60,
        )
        response.raise_for_status()
        return dict(response.json())


def qualifier_filter(qualifier: Optional[str]) -> List[Dict[str, Any]]:
    if not qualifier or qualifier in ("None", "Null"):
        return []
    return [{"match_phrase": {"qualifier": qualifier}}]


def components_query(product: str, version: str, qualifier: Optional[str], distribution_build_number: str, result: str) -> Dict[str, Any]:
    return {
        "size": 50,
        "_source": ["component"],
        "query": {
            "bool": {
                "filter": [
                    {"match_phrase": {"version": version}},
                    {"match_phrase": {"component_category": product}},
                    {"match_phrase": {"distribution_build_number": distribution_build_number}},
                    {"match_phrase": {"component_build_result": result}},
                ] + qualifier_filter(qualifier)
            }
        },
    }


def component_top_results_query(component: str, version: str, qualifier: Optional[str], distribution_build_number: str) -> Dict[str, Any]:
    source_fields = ["platform", "architecture", "distribution", "test_report_manifest_yml", "integ_test_build_url", "rc_number", "component_build_result"]
    return {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"match_phrase": {"component": component}},
                    {"match_phrase": {"version": version}},
                    {"match_phrase": {"distribution_build_number": distribution_build_number}},
                ] + qualifier_filter(qualifier)
            }
        },
        "aggs": {
            "unique_combinations": {
                "composite": {
                    "size": 100,
                    "sources": [
                        {"platform": {"terms": {"field": "platform"}}},
                        {"architecture": {"terms": {"field": "architecture"}}},
                        {"distribution": {"terms": {"field": "distribution"}}},
                    ],
                },
                "aggs": {
                    "latest_doc": {
                        "top_hits": {
                            "size": 1,
                            "sort": [{"integ_test_build_number": {"order": "desc"}}],
                            "_source": source_fields,
                        }
                    }
                },
            }
        },
    }


def release_owners_query(component: str, version: str) -> Dict[str, Any]:
    return {
        "size": 1,
        "_source": ["release_owners", "release_issue_exists"],
        "query": {
            "bool": {
                "filter": [
                    {"match_phrase": {"version": version}},
                    {"match_phrase": {"component.keyword": component}},
                ]
            }
        },
        "sort": [{"current_date": {"order": "desc"}}],
    }


def metrics_visualization_url(distribution: str, architecture: str, version: str, component: str) -> Optional[str]:
    panel_id = PANEL_IDS.get(f"{distribution}_{architecture}")
    if not panel_id:
        logging.warning(f"Unknown {distribution}_{architecture}, no metrics visualization panel available.")
        return None
    query_params = (
        "?_g=(filters:!(),refreshInterval:(pause:!t,value:0),time:(from:now-30d,to:now))"
        "&_a=(description:'OpenSearch%20Release%20Build%20and%20Integration%20Test%20Results',"
    )
    filter_template = (
        f"filters:!(('$state':(store:appState),meta:(alias:!n,disabled:!f,index:d90d2ba0-8fe0-11ef-a168-f19b1bbc360c,key:version,negate:!f,"
        f"params:(query:'{version}'),type:phrase),query:(match_phrase:(version:'{version}'))),"
        f"('$state':(store:appState),meta:(alias:!n,disabled:!f,index:d90d2ba0-8fe0-11ef-a168-f19b1bbc360c,key:component,negate:!f,"
        f"params:(query:{component}),type:phrase),query:(match_phrase:(component:{component})))),"
        "fullScreenMode:!f,options:(hidePanelTitles:!f,useMargins:!t),query:(language:kuery,query:''),timeRestore:!t,"
        "title:'OpenSearch%20Release%20Build%20and%20Integration%20Test%20Results',viewMode:view)"
    )
    return f"{METRICS_DASHBOARD_URL}{query_params}expandedPanelId:{panel_id},{filter_template}"


def markdown_table(version: str, rows: List[Dict[str, Any]], release_owners: List[str]) -> str:
    header = (
        f"\n### Integration Test Failed for version {version}. See the specifications below:\n"
        "\n#### Details\n"
        "\n| Platform | Dist | Arch | Dist Build No. | RC | Test Report | Workflow Run | Failing tests |\n"
        "|----------|------|------|----------------|----|-------------|--------------|---------------|\n"
    )
    table_rows = "\n".join(
        f"| {row['platform']} | {row['distribution']} | {row['architecture']} | {row['distribution_build_number']} | "
        f"{row['rc_number']} | {row['test_report_manifest_yml']} | {row['integ_test_build_url']} | "
        f"[Check metrics]({row['metrics_visualization_url']}) |"
        for row in rows
    )
    additional_information = (
        "\n\nCheck out test report manifest linked above for steps to reproduce, cluster and integration test failure logs. "
        "For additional information checkout the [wiki](https://github.com/opensearch-project/opensearch-build/wiki/Testing-the-Distribution) "
        "and [OpenSearch Metrics Dashboard](https://metrics.opensearch.org/_dashboards/app/dashboards#/view/21aad140-49f6-11ef-bbdd-39a9b324a5aa).\n"
    )
    if release_owners:
        tagged = " ".join(f"@{owner}" for owner in release_owners)
        additional_information = f"{additional_information}\nTagging the release owners to take a look {tagged}"
    return header + table_rows + additional_information


def failed_component_rows(client: MetricsClient, component: str, version: str, qualifier: Optional[str], distribution_build_number: str) -> List[Dict[str, Any]]:
    response = client.search(INTEG_TEST_INDEX, component_top_results_query(component, version, qualifier, distribution_build_number))
    buckets = response.get("aggregations", {}).get("unique_combinations", {}).get("buckets", [])
    rows = []
    for bucket in buckets:
        hits = bucket.get("latest_doc", {}).get("hits", {}).get("hits", [])
        if not hits:
            continue
        source = hits[0]["_source"]
        if source.get("component_build_result") != "failed":
            continue
        rows.append(
            {
                "platform": source.get("platform"),
                "distribution": source.get("distribution"),
                "architecture": source.get("architecture"),
                "test_report_manifest_yml": source.get("test_report_manifest_yml"),
                "integ_test_build_url": source.get("integ_test_build_url"),
                "distribution_build_number": distribution_build_number,
                "rc_number": source.get("rc_number"),
                "metrics_visualization_url": metrics_visualization_url(source.get("distribution"), source.get("architecture"), version, component),
            }
        )
    return rows


def release_owners(client: MetricsClient, component: str, version: str) -> List[str]:
    try:
        response = client.search(RELEASE_METRICS_INDEX, release_owners_query(component, version))
    except requests.RequestException as error:
        logging.warning(f"Unable to look up release owners for {component}: {error}")
        return []
    hits = response.get("hits", {}).get("hits", [])
    if not hits:
        return []
    owners = hits[0].get("_source", {}).get("release_owners") or []
    return [str(owner) for owner in owners]


def unique_components(response: Dict[str, Any]) -> List[str]:
    names = [hit["_source"]["component"] for hit in response.get("hits", {}).get("hits", [])]
    return sorted(set(names))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Collect integ-test failures for a distribution build.")
    parser.add_argument("--input-manifest", required=True, help="Path to the input manifest, e.g. manifests/2.0.0/opensearch-2.0.0.yml.")
    parser.add_argument("--distribution-number", required=True, help="Distribution build number used to run the integration tests.")
    parser.add_argument("--output-dir", required=True, help="Directory the report and markdown issue bodies are written to.")
    args = parser.parse_args()

    metrics_url = os.environ.get("METRICS_HOST_URL")
    if not metrics_url:
        logging.error("METRICS_HOST_URL is not set. The integration test results live on the OpenSearch metrics cluster and cannot be read without it.")
        return 1

    with open(args.input_manifest) as manifest_file:
        input_manifest = yaml.safe_load(manifest_file)

    version = str(input_manifest["build"]["version"])
    product = str(input_manifest["build"]["name"])
    qualifier = input_manifest["build"].get("qualifier")
    # gh --repo does not accept the trailing .git that the manifests carry.
    component_repositories = {component["name"]: str(component["repository"]).removesuffix(".git") for component in input_manifest.get("components", [])}

    os.makedirs(args.output_dir, exist_ok=True)
    client = MetricsClient(metrics_url)

    passed_components = unique_components(client.search(INTEG_TEST_INDEX, components_query(product, version, qualifier, args.distribution_number, "passed")))
    failed_components = unique_components(client.search(INTEG_TEST_INDEX, components_query(product, version, qualifier, args.distribution_number, "failed")))
    logging.info(f"Distribution Build Number: {args.distribution_number}")
    logging.info(f"Failed Components: {failed_components}")
    logging.info(f"Passed Components: {passed_components}")

    report: Dict[str, Any] = {
        "version": version,
        "product": product,
        "qualifier": qualifier,
        "distribution_build_number": args.distribution_number,
        "failed": [],
        "passed": [],
        "skipped": [],
    }

    for component in failed_components:
        repository = component_repositories.get(component)
        if not repository:
            logging.warning(f"{component} is not present in {args.input_manifest}, skipping issue creation.")
            report["skipped"].append({"component": component, "reason": "component not present in the input manifest"})
            continue
        rows = failed_component_rows(client, component, version, qualifier, args.distribution_number)
        if not rows:
            logging.info(f"Warning: Latest test run passed for {component} but found previous failures indicating flakiness. Skipping issue update.")
            report["skipped"].append({"component": component, "reason": "latest run per platform passed, flaky history only"})
            continue
        body_file = os.path.join(args.output_dir, f"{component}.md")
        with open(body_file, "w") as body:
            body.write(markdown_table(version, rows, release_owners(client, component, version)))
        report["failed"].append(
            {
                "component": component,
                "repository": repository,
                "title": f"[AUTOCUT] Integration Test Failed for {component}-{version}",
                "body_file": body_file,
                "labels": f"autocut,v{version}",
            }
        )

    for component in passed_components:
        if component in failed_components:
            continue
        repository = component_repositories.get(component)
        if not repository:
            continue
        report["passed"].append(
            {
                "component": component,
                "repository": repository,
                "title": f"[AUTOCUT] Integration Test Failed for {component}-{version}",
                "close_comment": f"Closing the issue as the integration tests for {component} passed for version: **{version}**.",
            }
        )

    report_file = os.path.join(args.output_dir, "report.json")
    with open(report_file, "w") as report_output:
        json.dump(report, report_output, indent=2)
    logging.info(f"Wrote {report_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
