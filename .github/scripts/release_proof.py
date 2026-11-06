#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Compares the build manifest of a GitHub Actions replay of a release candidate with the build manifest of the
Jenkins release candidate: same components, built from the same commits. Prints a markdown table and exits 1
on any difference. Only needs PyYAML and the standard library."""

import argparse
import sys
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import yaml


def load_manifest(location: str) -> Dict[str, Any]:
    if location.startswith(("http://", "https://")):
        with urllib.request.urlopen(location, timeout=60) as response:  # nosec B310 - public build manifests
            manifest: Dict[str, Any] = yaml.safe_load(response.read())
            return manifest
    with open(location) as f:
        manifest = yaml.safe_load(f)
        return manifest


def component_commits(manifest: Dict[str, Any]) -> Dict[str, str]:
    return {component["name"]: component.get("commit_id", "") for component in manifest.get("components", [])}


def compare(replay: Dict[str, Any], jenkins: Dict[str, Any], components: Optional[List[str]] = None) -> Tuple[List[str], int]:
    replay_build, jenkins_build = replay.get("build", {}), jenkins.get("build", {})
    differences = 0
    lines = ["| Field | Replay (GitHub Actions) | Jenkins | Result |", "| --- | --- | --- | --- |"]
    for field in ["name", "version", "qualifier", "platform", "architecture", "distribution"]:
        ok = replay_build.get(field) == jenkins_build.get(field)
        differences += not ok
        lines.append(f"| build.{field} | `{replay_build.get(field)}` | `{jenkins_build.get(field)}` | {'match' if ok else 'DIFFERS'} |")
    replay_commits, jenkins_commits = component_commits(replay), component_commits(jenkins)
    for name in components or list(replay_commits):
        mine, theirs = replay_commits.get(name), jenkins_commits.get(name)
        ok = mine is not None and mine == theirs
        differences += not ok
        lines.append(f"| {name} | `{mine or 'not built'}` | `{theirs or 'not in the Jenkins build'}` | {'match' if ok else 'DIFFERS'} |")
    return lines, differences


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", required=True, help="Build manifest of the GitHub Actions replay (path or URL).")
    parser.add_argument("--jenkins", required=True, help="Build manifest of the Jenkins release candidate (path or URL).")
    parser.add_argument("--components", nargs="*", help="Components to compare (default: every component of the replay).")
    args = parser.parse_args(argv)
    lines, differences = compare(load_manifest(args.replay), load_manifest(args.jenkins), args.components)
    print("\n".join(lines))
    print(f"\n{differences} difference(s).")
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main())
