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

from release_git_workflow.release_branch import plan_release_branches
from release_git_workflow.release_plan import RefPlan
from release_git_workflow.run_plan import apply_plan, write_outputs
from system import console


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan, or create from a saved plan, release branches in the component repositories.")
    parser.add_argument("--manifest", nargs="+", help="Build manifest URLs or input manifest paths relative to manifests/, e.g. 3.10.0/opensearch-3.10.0.yml.")
    parser.add_argument("--source-branch", default="main", help="Branch to cut from for input manifests.")
    parser.add_argument("--target-branch", help="Release branch to create, e.g. 3.10.")
    parser.add_argument("--apply", help="Create the branches listed in this plan file instead of planning.")
    parser.add_argument("--output", help="Write the plan to this JSON file.")
    parser.add_argument("--summary", help="Append the plan as markdown to this file, e.g. $GITHUB_STEP_SUMMARY.")
    args = parser.parse_args()
    console.configure(level=logging.INFO)

    if args.apply:
        with open(args.apply) as f:
            apply_plan(RefPlan.from_json(f.read()))
        return 0

    if not args.manifest or not args.target_branch:
        parser.error("--manifest and --target-branch are required to plan.")
    plan = plan_release_branches(args.manifest, args.source_branch, args.target_branch)
    write_outputs(plan, f"Release branch {args.target_branch}", args.output, args.summary)
    return 1 if plan.conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
