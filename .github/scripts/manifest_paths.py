#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Port of the Jenkins shared-library manifest classes used by the migrated GitHub Actions workflows.

The Jenkins pipelines under jenkins/opensearch/ read manifests through the Groovy classes
src/jenkins/BuildManifest.groovy and src/jenkins/InputManifest.groovy of
opensearch-project/opensearch-build-libraries. GitHub Actions has no equivalent of a shared Groovy
class path, so the handful of accessors the two migrated pipelines actually need are implemented here
and called from the composite actions under .github/actions/.

Every subcommand prints `key=value` lines so callers can redirect straight into $GITHUB_OUTPUT.
"""

import argparse
import json
import re
import sys
from typing import Any, Dict, List, Optional, cast

import yaml

DEFAULT_PUBLIC_ARTIFACT_URL = "https://ci.opensearch.org/ci/dbc"
DEFAULT_CI_IMAGE = "opensearchstaging/ci-runner:ci-runner-al2-opensearch-build-v1"
DEFAULT_CI_ARGS = "-e JAVA_HOME=/opt/java/openjdk-21"
DEFAULT_JAVA_VERSION = "openjdk-21"


def load(path: str) -> Dict[str, Any]:
    with open(path) as manifest_file:
        return cast(Dict[str, Any], yaml.safe_load(manifest_file))


def emit(values: Dict[str, str]) -> None:
    for key, value in values.items():
        print(f"{key}={value}")


def filename(name: str) -> str:
    """BuildManifest.Build.getFilename(): 'OpenSearch Dashboards' -> 'opensearch-dashboards'."""
    return name.lower().replace(" ", "-")


def extension(distribution: str) -> str:
    """BuildManifest.Build.getExtension()."""
    return distribution if distribution in ("zip", "rpm", "deb") else "tar.gz"


def package_name(build: Dict[str, Any]) -> str:
    """BuildManifest.Build.getPackageName()."""
    stem = "-".join([filename(build["name"]), build["version"], build["platform"], build["architecture"]])
    return f"{stem}.{extension(build['distribution'])}"


def min_artifact(manifest: Dict[str, Any]) -> str:
    """BuildManifest.getMinArtifact(): the first `dist` artifact of the core component."""
    core = manifest["build"]["name"].replace(" ", "-")
    for component in manifest.get("components") or []:
        if component.get("name") == core:
            artifacts = (component.get("artifacts") or {}).get("dist") or [""]
            return str(artifacts[0])
    return ""


def artifact_root(manifest: Dict[str, Any], job_name: str, build_number: str, build_feature: Optional[str] = None) -> str:
    """BuildManifest.getArtifactRoot()."""
    build = manifest["build"]
    return "/".join([job_name, build_feature or build["version"], build_number, build["platform"], build["architecture"], build["distribution"]])


def index_file_root(manifest: Dict[str, Any], job_name: str, build_feature: Optional[str] = None) -> str:
    """BuildManifest.getIndexFileRoot()."""
    build = manifest["build"]
    return "/".join([job_name, build_feature or build["version"], "index", build["platform"], build["architecture"], build["distribution"]])


def ci_image(manifest: Dict[str, Any], kind: str, platform: str, distribution: str) -> Dict[str, str]:
    """detectDockerAgent() / detectTestDockerAgent().

    The manifest schema kept the `ci.image` key but changed its shape from a single `{name, args}` to a
    per platform/distribution map, which InputManifest.Ci exposes as `images`
    (opensearch-build-libraries src/jenkins/InputManifest.groovy:29-68). The cut-over version differs per
    manifest kind: 1.2 for input manifests (detectDockerAgent.groovy:34) and 1.1 for test manifests
    (detectTestDockerAgent.groovy:94).
    """
    threshold = 1.2 if kind == "input" else 1.1
    schema_version = manifest.get("schema-version")
    ci = manifest.get("ci") or {}

    image, args = DEFAULT_CI_IMAGE, DEFAULT_CI_ARGS
    if schema_version is not None and float(schema_version) < threshold:
        legacy = ci.get("image") or {}
        image = legacy.get("name") or image
        args = legacy.get("args") or args
    else:
        entry = ((ci.get("image") or {}).get(platform) or {}).get(distribution) or {}
        image = entry.get("name") or image
        args = entry.get("args") or args

    java_match = re.search(r"openjdk-\d+", args)
    return {
        "image": image,
        "args": args,
        "java-version": java_match.group(0) if java_match else DEFAULT_JAVA_VERSION,
    }


# distribution-build.jenkinsfile:30-32 - Jenkins selected EC2 agents by label. The runner labels below are the
# GitHub-hosted equivalents; a fork with self-hosted runners should replace them (see docs/jenkins-to-actions.md).
RUNNERS = {
    "linux-x64": "ubuntu-24.04",
    "linux-arm64": "ubuntu-24.04-arm",
    "windows-x64": "windows-2022",
}

# distribution-build.jenkinsfile:239-957 - the stage list, in the order the stages appear. `archive` stages run
# buildAssembleUpload() on one agent; `package` stages build the tar on one agent and assemble the rpm/deb on
# another, which is why they carry two images.
BUILD_TARGETS = [
    {"platform": "linux", "architecture": "x64", "distribution": "tar", "kind": "archive"},
    {"platform": "linux", "architecture": "x64", "distribution": "rpm", "kind": "package"},
    {"platform": "linux", "architecture": "x64", "distribution": "deb", "kind": "package"},
    {"platform": "linux", "architecture": "arm64", "distribution": "tar", "kind": "archive"},
    {"platform": "linux", "architecture": "arm64", "distribution": "rpm", "kind": "package"},
    {"platform": "linux", "architecture": "arm64", "distribution": "deb", "kind": "package"},
    {"platform": "windows", "architecture": "x64", "distribution": "zip", "kind": "archive"},
]

SUPPORTED_PLATFORMS = ["linux", "windows"]
SUPPORTED_DISTRIBUTIONS = ["tar", "rpm", "deb", "zip"]


def verify_platform_distribution(name: str, value: str, allowed: List[str], required: bool) -> List[str]:
    """distribution-build.jenkinsfile:1160-1176 verifyParameterPlatformDistribution().

    Jenkins aborted the build for an empty required parameter or an unsupported entry. Here the same check runs in
    the verify-parameters job so the build matrix is never generated from a typo.
    """
    entries = value.split()
    if not entries:
        if required:
            raise SystemExit(f"Missing parameter '{name}' (possible entries: {' '.join(allowed)}).")
        return []
    for entry in entries:
        if entry not in allowed:
            raise SystemExit(f"Error parameter '{name}': {value} (possible entries: {' '.join(allowed)}).")
    return entries


def distribution_matrix(manifest: Dict[str, Any], build_platform: str, build_distribution: str) -> Dict[str, str]:
    """The `when { expression { params.BUILD_PLATFORM.contains(...) } }` guards of every build stage.

    Jenkins declared one stage per platform/architecture/distribution and skipped it at runtime; GitHub Actions
    cannot skip matrix entries, so the entries themselves are filtered here and the per-stage docker agent
    resolution of detectDockerAgent() (distribution-build.jenkinsfile:156-171) is inlined into each entry.
    """
    platforms = verify_platform_distribution("BUILD_PLATFORM", build_platform, SUPPORTED_PLATFORMS, required=True)
    distributions = verify_platform_distribution("BUILD_DISTRIBUTION", build_distribution, SUPPORTED_DISTRIBUTIONS, required=True)

    matrices: Dict[str, List[Dict[str, str]]] = {"archive": [], "package": []}
    for target in BUILD_TARGETS:
        if target["platform"] not in platforms or target["distribution"] not in distributions:
            continue
        runner = RUNNERS[f"{target['platform']}-{target['architecture']}"]
        agent = ci_image(manifest, "input", target["platform"], target["distribution"])
        tar_agent = ci_image(manifest, "input", target["platform"], "tar")
        package = target["kind"] == "package"
        # The rpm/deb build stage mixes the tar image with the package args and the assemble stage runs the package
        # image with no args at all (distribution-build.jenkinsfile:337-339, 373-376).
        matrices[str(target["kind"])].append(
            {
                "platform": str(target["platform"]),
                "architecture": str(target["architecture"]),
                "distribution": str(target["distribution"]),
                "runs-on": runner,
                "image": tar_agent["image"] if package else agent["image"],
                "args": agent["args"],
                "java-version": agent["java-version"],
                "assemble-image": agent["image"],
                "assemble-args": "",
            }
        )

    return {
        "archive-matrix": json.dumps({"include": matrices["archive"]}),
        "package-matrix": json.dumps({"include": matrices["package"]}),
        "archive-count": str(len(matrices["archive"])),
        "package-count": str(len(matrices["package"])),
    }


def component_names(manifest: Dict[str, Any]) -> List[str]:
    return [component["name"] for component in manifest.get("components") or []]


def integ_test_components(test_manifest: Dict[str, Any], build_manifest: Dict[str, Any], requested: str, distribution: str) -> List[str]:
    """integ-test.jenkinsfile:149-172.

    The requested components must exist in the test manifest, are skipped when the build manifest does
    not contain them, and cross-cluster-replication and query-insights are skipped for rpm and deb
    because multiple clusters cannot be formed on one host
    (https://github.com/opensearch-project/opensearch-build/issues/4610).
    """
    default_list = component_names(test_manifest)
    in_build_manifest = component_names(build_manifest)
    selected = requested.split() if requested.strip() else list(default_list)

    result = []
    for component in selected:
        if component not in default_list:
            raise SystemExit(f"{component} is not present in the test manifest")
        if component not in in_build_manifest:
            print(f"Skipping tests for {component} as is not present in the provided build manifest.", file=sys.stderr)
            continue
        if distribution in ("rpm", "deb") and component in ("cross-cluster-replication", "query-insights"):
            print(f"Skipping integTest for {distribution} distribution for {component}", file=sys.stderr)
            continue
        result.append(component)
    return result


def integ_test_paths(build_manifest: Dict[str, Any], job_name: str, public_artifact_url: str, local_path: str) -> Dict[str, str]:
    """runIntegTestScript.groovy generatePaths()/generateBasePaths()."""
    build = build_manifest["build"]
    build_id = str(build["id"])
    artifact_root_url = f"{public_artifact_url}/{artifact_root(build_manifest, job_name, build_id)}"
    latest_core_url = "/".join(
        [
            "https://ci.opensearch.org/ci/dbc/distribution-build-opensearch",
            build["version"],
            "latest",
            build["platform"],
            build["architecture"],
            build["distribution"],
        ]
    )

    if local_path:
        paths = f"opensearch={local_path}" if build["name"] == "OpenSearch" else f"opensearch={local_path} opensearch-dashboards={local_path}"
    elif build["name"] == "OpenSearch":
        paths = f"opensearch={artifact_root_url}"
    else:
        paths = f"opensearch={latest_core_url} opensearch-dashboards={artifact_root_url}"

    base_path = "/".join([public_artifact_url, job_name, build["version"], build_id, build["platform"], build["architecture"], build["distribution"]])
    # runIntegTestScript.groovy:32-33 pins JAVA_HOME only for the OpenSearch core distribution on a non-windows agent.
    return {
        "paths": paths,
        "base-path": base_path,
        "filename": filename(build["name"]),
        "platform": build["platform"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_info = subparsers.add_parser("build-manifest-info", help="Accessors of BuildManifest needed by the migrated workflows")
    build_info.add_argument("manifest")
    build_info.add_argument("--job-name", required=True)
    build_info.add_argument("--build-number", required=True)
    build_info.add_argument("--build-feature", default=None)
    build_info.add_argument("--public-artifact-url", default=DEFAULT_PUBLIC_ARTIFACT_URL)

    input_info = subparsers.add_parser("input-manifest-info", help="Accessors of InputManifest needed by the migrated workflows")
    input_info.add_argument("manifest")

    image = subparsers.add_parser("ci-image", help="detectDockerAgent / detectTestDockerAgent")
    image.add_argument("manifest")
    image.add_argument("--kind", choices=["input", "test"], default="input")
    image.add_argument("--platform", default="linux")
    image.add_argument("--distribution", default="tar")

    components = subparsers.add_parser("components", help="Component names of a manifest, as a JSON array")
    components.add_argument("manifest")

    integ_components = subparsers.add_parser("integ-test-components", help="Component matrix of the integ-test workflow")
    integ_components.add_argument("manifest", help="Test manifest")
    integ_components.add_argument("--build-manifest", required=True)
    integ_components.add_argument("--component-name", default="", help="Space separated component names, empty means every component of the test manifest")

    matrix = subparsers.add_parser("distribution-matrix", help="Build matrices of the distribution-build workflow")
    matrix.add_argument("manifest", help="Input manifest")
    matrix.add_argument("--build-platform", required=True)
    matrix.add_argument("--build-distribution", required=True)
    matrix.add_argument("--test-platform", default="")
    matrix.add_argument("--test-distribution", default="")

    paths = subparsers.add_parser("integ-test-paths", help="--paths and --base-path of test.sh integ-test")
    paths.add_argument("manifest", help="Build manifest")
    paths.add_argument("--job-name", default="distribution-build-opensearch")
    paths.add_argument("--public-artifact-url", default=DEFAULT_PUBLIC_ARTIFACT_URL)
    paths.add_argument("--local-path", default="")

    args = parser.parse_args()
    manifest = load(args.manifest)

    if args.command == "build-manifest-info":
        build = manifest["build"]
        root = artifact_root(manifest, args.job_name, args.build_number, args.build_feature)
        root_without_distribution = root.rsplit("/", 1)[0]
        emit(
            {
                "build-id": str(build["id"]),
                "name": build["name"],
                "version": build["version"],
                "platform": build["platform"],
                "architecture": build["architecture"],
                "distribution": build["distribution"],
                "filename": filename(build["name"]),
                "package-name": package_name(build),
                "min-artifact": min_artifact(manifest),
                "artifact-root": root,
                "artifact-root-url": f"{args.public_artifact_url}/{root}",
                "artifact-root-url-without-distribution": f"{args.public_artifact_url}/{root_without_distribution}",
                "index-file-root": index_file_root(manifest, args.job_name, args.build_feature),
                "components": json.dumps(component_names(manifest)),
            }
        )
    elif args.command == "input-manifest-info":
        build = manifest["build"]
        qualifier = build.get("qualifier")
        emit(
            {
                "name": build["name"],
                "version": build["version"],
                "qualifier": qualifier or "",
                # distribution-build.jenkinsfile:207 builds the revision as version + '-' + qualifier
                "revision": f"{build['version']}-{qualifier}" if qualifier else build["version"],
                "filename": filename(build["name"]),
                "components": json.dumps(component_names(manifest)),
            }
        )
    elif args.command == "ci-image":
        emit(ci_image(manifest, args.kind, args.platform, args.distribution))
    elif args.command == "components":
        print(json.dumps(component_names(manifest)))
    elif args.command == "integ-test-components":
        build_manifest = load(args.build_manifest)
        distribution = build_manifest["build"]["distribution"]
        emit(
            {
                "components": json.dumps(integ_test_components(manifest, build_manifest, args.component_name, distribution)),
                # integ-test.jenkinsfile:152 - rpm and deb install as root, so the tests run as uid 1000
                "switch-user-non-root": "true" if distribution in ("rpm", "deb") else "false",
            }
        )
    elif args.command == "distribution-matrix":
        # distribution-build.jenkinsfile:193-200 - the TEST parameters are only validated when they are set.
        verify_platform_distribution("TEST_PLATFORM", args.test_platform, SUPPORTED_PLATFORMS, required=False)
        verify_platform_distribution("TEST_DISTRIBUTION", args.test_distribution, SUPPORTED_DISTRIBUTIONS, required=False)
        emit(distribution_matrix(manifest, args.build_platform, args.build_distribution))
    elif args.command == "integ-test-paths":
        emit(integ_test_paths(manifest, args.job_name, args.public_artifact_url, args.local_path))

    return 0


if __name__ == "__main__":
    sys.exit(main())
