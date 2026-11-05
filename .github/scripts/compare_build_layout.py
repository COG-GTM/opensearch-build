#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Diff the file names and manifest layout of a GitHub Actions build against a Jenkins build.

Inputs are the `build-record` artifact of .github/workflows/_build-distribution.yml (file-list.txt plus
builds/<filename>/manifest.yml and dist/<filename>/manifest.yml) and the artifact root of a Jenkins
distribution-build-opensearch build. Versions (including -SNAPSHOT), build ids, commit SHAs and artifact roots are
normalised, and only the components both builds contain are compared, so a component-scoped build can be checked
against a full Jenkins build. Maven snapshot naming (timestamped file names and the per-version maven-metadata.xml a
snapshot repository adds) is normalised too, so a --snapshot build can be checked against a release-style Jenkins
build; the per-version metadata files are listed separately. Lists of plain values (artifact paths) are compared as
sorted sets because build.sh records them in filesystem order. Prints a unified diff per section; exits 1 when any
section differs.
"""

import argparse
import difflib
import re
import sys
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Set

import yaml

SHA = re.compile(r"\b[0-9a-f]{40}\b")
SNAPSHOT = re.compile(r"\{version\}(\.0)?-(?:SNAPSHOT|\d{8}\.\d{6}-\d+)")
SNAPSHOT_METADATA = re.compile(r"/\{version\}(?:\.0)?/maven-metadata\.xml(\.\w+)?$")
INFORMATIONAL = ("shared components", "snapshot-only files")


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as response:  # nosec B310 - https artifact URLs only
        body: bytes = response.read()
    return body.decode("utf-8")


class Normalizer:
    def __init__(self, manifest: Dict[str, Any], root_url: Optional[str] = None) -> None:
        build = manifest["build"]
        self.version = re.sub(r"-SNAPSHOT$", "", str(build["version"]))
        self.build_id = str(build.get("id", ""))
        self.root_url = root_url.rstrip("/") if root_url else None

    def __call__(self, value: str) -> str:
        if self.root_url:
            value = value.replace(self.root_url, "{root}")
        value = SHA.sub("{sha}", value)
        value = SNAPSHOT.sub(r"{version}\1", value.replace(self.version, "{version}"))
        if self.build_id:
            value = re.sub(rf"(?<![0-9]){re.escape(self.build_id)}(?![0-9])", "{id}", value)
        return value


def layout(node: Any, normalize: Callable[[str], str], prefix: str = "") -> List[str]:
    """Key paths of a manifest with string leaves normalised; list items keep their position."""
    lines: List[str] = []
    if isinstance(node, dict):
        for key in node:
            lines.extend(layout(node[key], normalize, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(node, list) and node and not any(isinstance(item, (dict, list)) for item in node):
        lines.extend(sorted(f"{prefix}[]: {normalize(str(item))}" for item in node))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            lines.extend(layout(item, normalize, f"{prefix}[{index}]"))
    else:
        lines.append(f"{prefix}: {normalize(str(node))}")
    return lines


def only_components(manifest: Dict[str, Any], names: Set[str]) -> Dict[str, Any]:
    scoped = dict(manifest)
    scoped["components"] = [component for component in manifest.get("components") or [] if component["name"] in names]
    return scoped


def artifact_files(manifest: Dict[str, Any], filename: str) -> List[str]:
    files = [f"builds/{filename}/manifest.yml"]
    for component in manifest.get("components") or []:
        for paths in (component.get("artifacts") or {}).values():
            files.extend(f"builds/{filename}/{path}" for path in paths)
    return files


def diff(title: str, jenkins: List[str], actions: List[str]) -> List[str]:
    return list(difflib.unified_diff(jenkins, actions, fromfile=f"jenkins/{title}", tofile=f"actions/{title}", lineterm=""))


def compare(record: str, jenkins_root: str, get: Callable[[str], str] = fetch) -> Dict[str, List[str]]:
    with open(f"{record}/file-list.txt") as handle:
        actions_files = [line.strip() for line in handle if line.strip()]
    filename = actions_files[0].split("/")[1]
    with open(f"{record}/builds/{filename}/manifest.yml") as handle:
        actions_build = yaml.safe_load(handle)
    with open(f"{record}/dist/{filename}/manifest.yml") as handle:
        actions_dist = yaml.safe_load(handle)
    jenkins_build = yaml.safe_load(get(f"{jenkins_root}/builds/{filename}/manifest.yml"))
    jenkins_dist = yaml.safe_load(get(f"{jenkins_root}/dist/{filename}/manifest.yml"))

    shared = {c["name"] for c in actions_build.get("components") or []} & {c["name"] for c in jenkins_build.get("components") or []}
    norm_actions = Normalizer(actions_build, actions_dist["build"]["location"].split("/dist/")[0])
    norm_jenkins = Normalizer(jenkins_build, jenkins_root)

    jenkins_build, jenkins_dist = only_components(jenkins_build, shared), only_components(jenkins_dist, shared)
    actions_build, actions_dist = only_components(actions_build, shared), only_components(actions_dist, shared)

    def snapshot_only(lines: List[str]) -> List[str]:
        return [line for line in lines if SNAPSHOT_METADATA.search(line)]

    def without_snapshot_only(lines: List[str]) -> List[str]:
        return [line for line in lines if not SNAPSHOT_METADATA.search(line)] if snapshot_vs_release else lines

    snapshot_vs_release = str(actions_build["build"]["version"]).endswith("-SNAPSHOT") and not str(jenkins_build["build"]["version"]).endswith("-SNAPSHOT")
    actions_names = sorted(norm_actions(f) for f in actions_files)
    package = jenkins_dist["build"]["location"].rsplit("/", 1)[1]
    jenkins_files = sorted({norm_jenkins(f) for f in artifact_files(jenkins_build, filename)} | {
        f"dist/{filename}/manifest.yml", f"dist/{filename}/{norm_jenkins(package)}"})
    return {
        "shared components": [", ".join(sorted(shared)) or "(none)"],
        "snapshot-only files": [f"{len(snapshot_only(actions_names))} per-version maven-metadata.xml files" if snapshot_vs_release else "(none)"],
        "file names": diff("files", jenkins_files, without_snapshot_only(actions_names)),
        "build manifest": diff("builds/manifest.yml", layout(jenkins_build, norm_jenkins), without_snapshot_only(layout(actions_build, norm_actions))),
        "bundle manifest": diff("dist/manifest.yml", layout(jenkins_dist, norm_jenkins), layout(actions_dist, norm_actions)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", required=True, help="Unpacked build-record artifact")
    parser.add_argument("--jenkins-root", required=True, help="Jenkins artifact root, e.g. .../distribution-build-opensearch/3.9.0/12228/linux/x64/tar")
    args = parser.parse_args()
    if not args.jenkins_root.startswith("https://"):
        parser.error("--jenkins-root must be an https:// URL")

    sections = compare(args.record, args.jenkins_root.rstrip("/"))
    different = False
    for title, lines in sections.items():
        print(f"## {title}")
        if title in INFORMATIONAL:
            print(lines[0])
        elif lines:
            different = True
            print("\n".join(lines))
        else:
            print("identical")
        print()
    return 1 if different else 0


if __name__ == "__main__":
    sys.exit(main())
