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

from release_git_workflow.release_manifest_lock import ACTIONS, ReleaseManifestLock
from system import console


def main() -> int:
    parser = argparse.ArgumentParser(description="Lock the refs in the input manifests of a release.")
    parser.add_argument("--version", required=True, help="Release version, e.g. 3.10.0.")
    parser.add_argument("--action", required=True, choices=ACTIONS, help="How to lock the refs.")
    parser.add_argument("--components", default="", help="Comma-separated component names to lock, all when empty.")
    parser.add_argument("--opensearch-rc", default="", help="OpenSearch release candidate build number, for MATCH_BUILD_MANIFEST.")
    parser.add_argument("--opensearch-dashboards-rc", default="", help="OpenSearch Dashboards release candidate build number, for MATCH_BUILD_MANIFEST.")
    parser.add_argument("--summary", help="Append the locked refs as markdown to this file, e.g. $GITHUB_STEP_SUMMARY.")
    args = parser.parse_args()
    console.configure(level=logging.INFO)

    lock = ReleaseManifestLock(
        args.version,
        args.action,
        args.components.split(","),
        {"opensearch": args.opensearch_rc, "opensearch-dashboards": args.opensearch_dashboards_rc},
    )
    changes = lock.run()
    lines = [f"### Manifest commit lock for {args.version} ({args.action})", "", f"{len(changes)} ref(s) changed.", ""]
    if changes:
        lines += ["| Manifest | Component | Old ref | New ref |", "| --- | --- | --- | --- |"]
        lines += [f"| {change.manifest} | {change.component} | `{change.old_ref}` | `{change.new_ref}` |" for change in changes]
    markdown = "\n".join(lines) + "\n"
    if args.summary:
        with open(args.summary, "a") as f:
            f.write(markdown + "\n")
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
