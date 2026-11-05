#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Port of the Jenkins shared-library manifest classes used by the composite actions under .github/actions/.

The Jenkins pipelines read manifests through the Groovy classes src/jenkins/BuildManifest.groovy and
src/jenkins/InputManifest.groovy of opensearch-project/opensearch-build-libraries. GitHub Actions has no
shared Groovy class path, so the accessors the composite actions need are implemented here. The script only
needs PyYAML, so it runs on a bare runner or inside any CI-runner image without `pipenv install`.

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


# GitHub Actions rejects `--network` and `--entrypoint` in jobs.<id>.container.options, and the runner appends its
# own `--entrypoint tail -f /dev/null` regardless, so the systemd entrypoint the rpm/deb test images carry cannot be
# honoured (docs/jenkins-to-actions.md). Those options are dropped with a warning instead of failing the parse.
UNSUPPORTED_CONTAINER_OPTIONS = ("--entrypoint", "--network")


def sanitize_container_options(args: str) -> str:
    """Filter manifest ci.image args down to options GitHub Actions accepts in a job container."""
    tokens = args.split()
    kept: List[str] = []
    skip_value = False
    for token in tokens:
        if skip_value:
            skip_value = False
            continue
        if any(character in '"\\' or character < " " for character in token):
            # The options end up inside a hand-built JSON container object built with format(), which does no escaping,
            # so a quote, a backslash or a control character would break the fromJSON() parse of the caller.
            raise ValueError(f"container option {token!r} contains a character that cannot be passed through JSON")
        name = token.split("=", 1)[0]
        if name in UNSUPPORTED_CONTAINER_OPTIONS:
            print(f"Dropping container option {token}: GitHub Actions does not support it.", file=sys.stderr)
            skip_value = "=" not in token
            continue
        kept.append(token)
    return " ".join(kept)


# Docker reference grammar: registry host, optional port, path components, and an optional :tag or @digest.
CI_IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*(:[A-Za-z0-9][A-Za-z0-9._-]*)?(@sha256:[a-f0-9]{64})?$")


def validate_ci_image(image: str) -> str:
    """Reject an image name that could not be a docker reference.

    The name is spliced into the hand-built JSON container object of the caller together with the options, so a quote
    in it would let a manifest control the whole object, options included.
    """
    if not CI_IMAGE_PATTERN.match(image):
        raise ValueError(f"container image {image!r} is not a valid docker image reference")
    return image


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
        "image": validate_ci_image(image),
        "args": sanitize_container_options(args),
        "java-version": java_match.group(0) if java_match else DEFAULT_JAVA_VERSION,
    }


def component_names(manifest: Dict[str, Any]) -> List[str]:
    return [component["name"] for component in manifest.get("components") or []]


# docs/adr/0001-gha-runners-and-secrets.md, label map: Jenkins agent label -> full `runs-on` label array of the
# ephemeral self-hosted pool. Only the Docker hosts are listed; single-host, benchmark and macOS pools are picked
# by the workflows that need them.
RUNS_ON = {
    "linux-x64": ["self-hosted", "linux", "x64", "al2023-x64-m5-4xlarge-docker-host"],
    "linux-arm64": ["self-hosted", "linux", "arm64", "al2023-arm64-m6g-4xlarge-docker-host"],
    "windows-x64": ["self-hosted", "windows", "x64", "windows2019-x64-m5-4xlarge-docker-host"],
}


def runs_on(platform: str, architecture: str) -> str:
    key = f"{platform}-{architecture}"
    if key not in RUNS_ON:
        raise ValueError(f"no runner pool for {key}; known: {', '.join(sorted(RUNS_ON))}")
    return json.dumps(RUNS_ON[key], separators=(",", ":"))


def build_manifest_info(manifest: Dict[str, Any], job_name: str, build_number: str, build_feature: Optional[str], public_artifact_url: str) -> Dict[str, str]:
    build = manifest["build"]
    root = artifact_root(manifest, job_name, build_number, build_feature)
    root_without_distribution = root.rsplit("/", 1)[0]
    return {
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
        "artifact-root-url": f"{public_artifact_url}/{root}",
        "artifact-root-url-without-distribution": f"{public_artifact_url}/{root_without_distribution}",
        "index-file-root": index_file_root(manifest, job_name, build_feature),
        "components": json.dumps(component_names(manifest)),
        "runs-on": runs_on(build["platform"], build["architecture"]),
    }


def input_manifest_info(manifest: Dict[str, Any]) -> Dict[str, str]:
    build = manifest["build"]
    qualifier = build.get("qualifier")
    return {
        "name": build["name"],
        "version": build["version"],
        "qualifier": qualifier or "",
        # distribution-build.jenkinsfile builds the revision as version + '-' + qualifier
        "revision": f"{build['version']}-{qualifier}" if qualifier else build["version"],
        "filename": filename(build["name"]),
        "components": json.dumps(component_names(manifest)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_info = subparsers.add_parser("build-manifest-info", help="Accessors of BuildManifest")
    build_info.add_argument("manifest")
    build_info.add_argument("--job-name", required=True)
    build_info.add_argument("--build-number", default=None, help="Defaults to build.id of the manifest")
    build_info.add_argument("--build-feature", default=None)
    build_info.add_argument("--public-artifact-url", default=DEFAULT_PUBLIC_ARTIFACT_URL)

    input_info = subparsers.add_parser("input-manifest-info", help="Accessors of InputManifest")
    input_info.add_argument("manifest")

    image = subparsers.add_parser("ci-image", help="detectDockerAgent / detectTestDockerAgent")
    image.add_argument("manifest")
    image.add_argument("--kind", choices=["input", "test"], default="input")
    image.add_argument("--platform", default="linux")
    image.add_argument("--distribution", default="tar")

    args = parser.parse_args()
    manifest = load(args.manifest)

    if args.command == "build-manifest-info":
        build_number = args.build_number or str(manifest["build"]["id"])
        emit(build_manifest_info(manifest, args.job_name, build_number, args.build_feature, args.public_artifact_url))
    elif args.command == "input-manifest-info":
        emit(input_manifest_info(manifest))
    else:
        emit(ci_image(manifest, args.kind, args.platform, args.distribution))

    return 0


if __name__ == "__main__":
    sys.exit(main())
