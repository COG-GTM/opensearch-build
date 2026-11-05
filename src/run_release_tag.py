#!/usr/bin/env python
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import argparse
import logging
import sys

from release_git_workflow.release_plan import RefPlan
from release_git_workflow.release_tag import distribution_manifest, plan_release_tags
from release_git_workflow.release_version import PRODUCTS
from release_git_workflow.run_plan import apply_plan, write_outputs
from system import console


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan, or create from a saved plan, the release tags in the component repositories.")
    parser.add_argument("--version", help="Release version, e.g. 3.10.0.")
    parser.add_argument("--opensearch-build", help="Tag from this OpenSearch release candidate build instead of the published release bundle.")
    parser.add_argument("--opensearch-dashboards-build", help="Tag from this OpenSearch Dashboards release candidate build instead of the published release bundle.")
    parser.add_argument("--apply", help="Create the tags listed in this plan file instead of planning.")
    parser.add_argument("--output", help="Write the plan to this JSON file.")
    parser.add_argument("--summary", help="Append the plan as markdown to this file, e.g. $GITHUB_STEP_SUMMARY.")
    args = parser.parse_args()
    console.configure(level=logging.INFO)

    if args.apply:
        with open(args.apply) as f:
            apply_plan(RefPlan.from_json(f.read()))
        return 0

    if not args.version:
        parser.error("--version is required to plan.")
    builds = dict(zip(PRODUCTS, [args.opensearch_build, args.opensearch_dashboards_build]))
    plan = RefPlan("tag")
    for product in PRODUCTS:
        plan_release_tags(distribution_manifest(product, args.version, builds[product]), args.version, plan)
    write_outputs(plan, f"Release tags for {args.version}", args.output, args.summary)
    return 1 if plan.conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
