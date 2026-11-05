#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Port of the record generation inside publishDistributionBuildResults() and publishIntegTestResults().

Those two shared-library steps (opensearch-build-libraries vars/) build OpenSearch `_bulk` bodies in Groovy
and then curl them into the metrics cluster. The curl part stayed in the composite actions under
.github/actions/ because it is identical to the Jenkins shell block; the record generation lives here so
that the field names, index names and aliases can be diffed against the Groovy source.

The generated files are ndjson, ready for `POST <index>/_bulk`.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple, cast

import yaml

BUILD_RESULTS_ALIAS = "opensearch-distribution-build-results"
INTEG_RESULTS_ALIAS = "opensearch-integration-test-results"
INTEG_FAILURES_ALIAS = "opensearch-integration-test-failures"


def monthly_index(alias: str) -> str:
    """publishDistributionBuildResults.groovy:33 - the index is suffixed with SimpleDateFormat('MM-yyyy')."""
    return f"{alias}-{datetime.now(timezone.utc).strftime('%m-%Y')}"


def repo_name(repository: str) -> str:
    return repository.rstrip("/").split("/")[-1].replace(".git", "")


def repo_url(repository: str) -> str:
    return repository[repository.index("github.com"):].replace(".git", "") if "github.com" in repository else repository


def bulk_line(index: str, document: Dict[str, Any]) -> str:
    return json.dumps({"index": {"_index": index}}) + "\n" + json.dumps(document) + "\n"


def extract_components(log_text: str, pattern: str) -> List[str]:
    """publishDistributionBuildResults.groovy:extractComponents().

    Jenkins collected the matching log lines with buildMessage(search: ...) and then took the first token of
    the text following the search string. Here the same regular expressions run over the build logs the
    build-manifest action captured with tee.
    """
    components = []
    for match in re.findall(pattern, log_text):
        component = match.split(" ")[0].split(",")[0].strip()
        if component and component not in components:
            components.append(component)
    return components


def distribution_build_records(
    index: str,
    input_manifest: Dict[str, Any],
    log_text: str,
    build_number: str,
    build_url: str,
    build_start_time: int,
    rc_number: int,
    component_category: str,
    overall_build_result: str,
) -> str:
    failed = extract_components(log_text, r"(?<=\bError building\s).*")
    passed = extract_components(log_text, r"(?<=\bSuccessfully built\s).*")
    build = input_manifest["build"]

    body = ""
    for component in input_manifest.get("components") or []:
        name = component["name"]
        if name in failed:
            result = "failed"
        elif name in passed:
            result = "passed"
        else:
            continue
        body += bulk_line(
            index,
            {
                "component": name,
                "component_repo": repo_name(component["repository"]),
                "component_repo_url": repo_url(component["repository"]),
                "component_ref": component.get("ref"),
                "version": build["version"],
                "qualifier": build.get("qualifier") or "None",
                "distribution_build_number": int(build_number),
                "distribution_build_url": build_url,
                "build_start_time": build_start_time,
                "rc": rc_number > 0,
                "rc_number": rc_number,
                "component_category": component_category,
                "component_build_result": result,
                "overall_build_result": overall_build_result,
            },
        )
    return body


def config_of(component: Dict[str, Any], name: str) -> Dict[str, Any]:
    for config in component.get("configs") or []:
        if config.get("name") == name:
            return cast(Dict[str, Any], config)
    return {}


def integ_test_records(
    results_index: str,
    failures_index: str,
    report: Dict[str, Any],
    job_name: str,
    integ_test_build_number: str,
    integ_test_build_url: str,
    distribution_build_url: str,
    build_start_time: int,
) -> Dict[str, str]:
    """publishIntegTestResults.groovy:37-110, one results record and N failure records per component."""
    full_version = str(report["version"])
    tokens = full_version.split("-")
    version = tokens[0]
    # publishIntegTestResults.groovy:49 - the qualifier is still embedded in the version
    # (https://github.com/opensearch-project/opensearch-build/issues/5386).
    qualifier = tokens[1] if len(tokens) > 1 else "None"
    distribution_build_number = report["id"]
    rc_number = int(report["rc"])
    platform, architecture, distribution = report["platform"], report["architecture"], report["distribution"]
    report_url = "/".join(
        [
            "https://ci.opensearch.org/ci/dbc",
            job_name,
            full_version,
            str(distribution_build_number),
            platform,
            architecture,
            distribution,
            "test-results",
            integ_test_build_number,
            "integ-test",
            "test-report.yml",
        ]
    )

    results_body, failures_body = "", ""
    for component in report.get("components") or []:
        common = {
            "component": component["name"],
            "component_repo": repo_name(component["repository"]),
            "component_repo_url": repo_url(component["repository"]),
            "version": version,
            "qualifier": qualifier,
            "integ_test_build_number": int(integ_test_build_number),
            "integ_test_build_url": integ_test_build_url,
            "distribution_build_number": int(distribution_build_number),
            "distribution_build_url": distribution_build_url,
            "build_start_time": build_start_time,
            "rc": rc_number > 0,
            "rc_number": rc_number,
            "platform": platform,
            "architecture": architecture,
            "distribution": distribution,
            "component_category": report["name"],
        }

        with_security = config_of(component, "with-security")
        without_security = config_of(component, "without-security")
        with_status = (with_security.get("status") or "unknown").lower()
        without_status = (without_security.get("status") or "unknown").lower()
        component_result = "failed" if {"fail", "not available"} & {with_status, without_status} else "passed"

        results_body += bulk_line(
            results_index,
            {
                **common,
                "component_build_result": component_result,
                "test_report_manifest_yml": report_url,
                "with_security": with_status,
                "with_security_build_yml": with_security.get("yml") or "",
                "with_security_cluster_stdout": with_security.get("cluster_stdout") or [],
                "with_security_cluster_stderr": with_security.get("cluster_stderr") or [],
                "with_security_test_stdout": with_security.get("test_stdout") or "",
                "with_security_test_stderr": with_security.get("test_stderr") or "",
                "without_security": without_status,
                "without_security_build_yml": without_security.get("yml") or "",
                "without_security_cluster_stdout": without_security.get("cluster_stdout") or [],
                "without_security_cluster_stderr": without_security.get("cluster_stderr") or [],
                "without_security_test_stdout": without_security.get("test_stdout") or "",
                "without_security_test_stderr": without_security.get("test_stderr") or "",
            },
        )

        for security_type, config in (("with-security", with_security), ("without-security", without_security)):
            for test_class, test_name in failed_tests(config.get("failed_test") or []):
                failures_body += bulk_line(
                    failures_index,
                    {**common, "test_type": security_type, "test_class": test_class, "test_name": test_name},
                )

    return {"results": results_body, "failures": failures_body}


def failed_tests(entries: List[str]) -> List[Tuple[str, str]]:
    """publishIntegTestResults.groovy:processFailedTests(), including its three sentinel values."""
    if not entries or "No Failed Test" in entries:
        return []
    if "Test Result Not Available" in entries:
        return [("Result Not Available", "Result Not Available")]
    if "Test Result Files List Not Available" in entries:
        return [("Report Not Available", "Report Not Available")]

    tests = []
    for entry in entries:
        parts = entry.split("#")
        test_class = parts[0].strip()
        tests.append((test_class, parts[1].strip() if len(parts) > 1 else test_class))
    return tests


def load(path: str) -> Dict[str, Any]:
    with open(path) as manifest_file:
        return cast(Dict[str, Any], yaml.safe_load(manifest_file))


def read_logs(paths: List[str]) -> str:
    text = ""
    for path in paths:
        try:
            with open(path, errors="replace") as log_file:
                text += log_file.read()
        except FileNotFoundError:
            print(f"Build log {path} not found, skipping.", file=sys.stderr)
    return text


def write(path: str, body: str) -> None:
    with open(path, "w") as output_file:
        output_file.write(body)
    print(f"Wrote {path} ({len(body)} bytes)", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("distribution-build-results", help="publishDistributionBuildResults()")
    build.add_argument("--input-manifest", required=True)
    build.add_argument("--build-log", action="append", default=[], help="Build logs captured by the build-manifest action")
    build.add_argument("--build-number", required=True)
    build.add_argument("--build-url", required=True)
    build.add_argument("--build-start-time", required=True, type=int, help="Epoch milliseconds")
    build.add_argument("--rc-number", required=True, type=int)
    build.add_argument("--component-category", default="OpenSearch")
    build.add_argument("--overall-build-result", default="SUCCESS")
    build.add_argument("--output", default="test-records.json")

    integ = subparsers.add_parser("integ-test-results", help="publishIntegTestResults()")
    integ.add_argument("--test-report", required=True)
    integ.add_argument("--job-name", default="integ-test")
    integ.add_argument("--integ-test-build-number", required=True)
    integ.add_argument("--integ-test-build-url", required=True)
    integ.add_argument("--distribution-build-url", required=True)
    integ.add_argument("--build-start-time", required=True, type=int, help="Epoch milliseconds")
    integ.add_argument("--output", default="test-records.json")
    integ.add_argument("--failures-output", default="test-failures.json")

    args = parser.parse_args()

    if args.command == "distribution-build-results":
        # Computed once so the `_index` of every bulk line and the index the composite action creates cannot straddle a
        # month boundary.
        index = monthly_index(BUILD_RESULTS_ALIAS)
        body = distribution_build_records(
            index,
            load(args.input_manifest),
            read_logs(args.build_log),
            args.build_number,
            args.build_url,
            args.build_start_time,
            args.rc_number,
            args.component_category,
            args.overall_build_result,
        )
        write(args.output, body)
        print(f"index={index}")
        print(f"alias={BUILD_RESULTS_ALIAS}")
    else:
        results_index = monthly_index(INTEG_RESULTS_ALIAS)
        failures_index = monthly_index(INTEG_FAILURES_ALIAS)
        bodies = integ_test_records(
            results_index,
            failures_index,
            load(args.test_report),
            args.job_name,
            args.integ_test_build_number,
            args.integ_test_build_url,
            args.distribution_build_url,
            args.build_start_time,
        )
        write(args.output, bodies["results"])
        write(args.failures_output, bodies["failures"])
        print(f"index={results_index}")
        print(f"alias={INTEG_RESULTS_ALIAS}")
        print(f"failures-index={failures_index}")
        print(f"failures-alias={INTEG_FAILURES_ALIAS}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
