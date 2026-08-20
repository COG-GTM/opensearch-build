#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Helpers for the update-build-failure-issues composite action.

Ports the query building of src/jenkins/ComponentBuildStatus.groovy and the component/issue bookkeeping of
vars/updateBuildFailureIssues.groovy from opensearch-build-libraries. The HTTP call to the metrics cluster and the
`gh issue` calls stay in the composite action, because those are the parts GitHub Actions already has tooling for.
"""

import argparse
import json
import sys
from typing import Any, Dict, List

import yaml


def search_query(product: str, version: str, qualifier: str, build_number: str, result: str, time_from: str, time_to: str) -> Dict[str, Any]:
    """ComponentBuildStatus.getQuery(): filter on category, result, version, build number and build_start_time."""
    filters: List[Dict[str, Any]] = [
        {"match_phrase": {"component_category": product}},
        {"match_phrase": {"component_build_result": result}},
        {"match_phrase": {"version": version}},
        {"match_phrase": {"distribution_build_number": build_number}},
        {"range": {"build_start_time": {"from": time_from, "to": time_to}}},
    ]
    # ComponentBuildStatus.isNullOrEmpty() treats 'None'/'Null' as absent.
    if qualifier and qualifier not in ("None", "Null"):
        filters.append({"match_phrase": {"qualifier": qualifier}})
    return {"_source": ["component"], "size": 1000, "query": {"bool": {"filter": filters}}}


def components_from_response(response: Dict[str, Any]) -> List[str]:
    hits = response.get("hits", {}).get("hits", [])
    return sorted({hit["_source"]["component"] for hit in hits})


def read_list(path: str) -> List[str]:
    with open(path) as list_file:
        return [line.strip() for line in list_file if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    query = subparsers.add_parser("query", help="Print the component build status search query")
    query.add_argument("--input-manifest", required=True)
    query.add_argument("--distribution-build-number", required=True)
    query.add_argument("--result", required=True, choices=["passed", "failed"])
    query.add_argument("--build-start-time-from", default="now-6h")
    query.add_argument("--build-start-time-to", default="now")

    components = subparsers.add_parser("components", help="Extract component names from a search response")
    components.add_argument("--response", required=True)

    plan = subparsers.add_parser("plan", help="Print 'create|close<TAB>repository<TAB>component' lines")
    plan.add_argument("--input-manifest", required=True)
    plan.add_argument("--failed", required=True, help="File with the failed component names")
    plan.add_argument("--passed", required=True, help="File with the passed component names")

    args = parser.parse_args()

    if args.command == "components":
        with open(args.response) as response_file:
            print("\n".join(components_from_response(json.load(response_file))))
        return 0

    with open(args.input_manifest) as manifest_file:
        manifest = yaml.safe_load(manifest_file)
    build = manifest["build"]

    if args.command == "query":
        print(
            json.dumps(
                search_query(
                    build["name"],
                    build["version"],
                    build.get("qualifier") or "None",
                    args.distribution_build_number,
                    args.result,
                    args.build_start_time_from,
                    args.build_start_time_to,
                )
            )
        )
        return 0

    failed, passed = read_list(args.failed), read_list(args.passed)
    for component in manifest.get("components") or []:
        name = component["name"]
        if name in failed:
            print(f"create\t{component['repository']}\t{name}")
        elif name in passed:
            print(f"close\t{component['repository']}\t{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
