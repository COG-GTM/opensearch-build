#!/usr/bin/env python3
"""Read the release-manifest fields used by the promotion workflows."""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import yaml


def load_manifest(path: str) -> Dict[str, Any]:
    """Load a YAML manifest."""
    with Path(path).open(encoding="utf-8") as manifest_file:
        data = yaml.safe_load(manifest_file)
    if not isinstance(data, dict):
        raise ValueError(f"Manifest {path} is not a mapping")
    return data


def print_facts(facts: Dict[str, str]) -> None:
    """Print facts in GitHub Actions output-file format."""
    for key, value in facts.items():
        print(f"{key}={value}")


def input_manifest_facts(path: str) -> None:
    """Print the InputManifest accessors used by promotion jobs."""
    manifest = load_manifest(path)
    build = manifest["build"]
    name = str(build["name"]).lower().replace(" ", "-")
    version = str(build["version"])
    qualifier = str(build.get("qualifier") or "")
    revision = f"{version}-{qualifier}" if qualifier else version
    major_version = version.split(".")[0]
    signing_email = "release@opensearch.org" if int(major_version) > 2 else "opensearch@amazon.com"
    print_facts(
        {
            "filename": name,
            "version": version,
            "qualifier": qualifier,
            "revision": revision,
            "major-version": major_version,
            "signing-email": signing_email,
            "repo-version": f"{major_version}.x",
        }
    )


def build_manifest_core_plugins(path: str) -> None:
    """Print the core-plugin sub-paths a build manifest advertises."""
    manifest = load_manifest(path)
    components = manifest["components"]
    if not isinstance(components, list):
        raise ValueError(f"Manifest {path} has no component list")
    if not components:
        return
    artifacts = components[0].get("artifacts") or {}
    for plugin in artifacts.get("core-plugins") or []:
        print(plugin)


def component_tag_version(version: str, component: str) -> str:
    """Reproduce createReleaseTag.groovy's component tag rule."""
    if "-" in version:
        tag_version = f"{version.split('-')[0]}.0-{version.split('-')[-1]}"
    else:
        tag_version = f"{version}.0"
    if component in {"OpenSearch", "OpenSearch-Dashboards", "functionalTestDashboards"}:
        tag_version = version
    return tag_version


def bundle_manifest_components(path: str, version: str = "") -> None:
    """Print release-tag component data as a JSON list."""
    manifest = load_manifest(path)
    build = manifest["build"]
    version = version or str(build["version"])
    components: List[Dict[str, str]] = []
    for component in manifest["components"]:
        name = str(component["name"])
        components.append(
            {
                "name": name,
                "repo": str(component["repository"]),
                "commit_id": str(component["commit_id"]),
                "tag_version": component_tag_version(version, name),
            }
        )
    print(json.dumps(components, separators=(",", ":")))


def main() -> None:
    """Parse arguments and print the requested manifest facts."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    input_parser = subparsers.add_parser("input-manifest-facts")
    input_parser.add_argument("--manifest", required=True)

    bundle_parser = subparsers.add_parser("bundle-manifest-components")
    bundle_parser.add_argument("--manifest", required=True)
    bundle_parser.add_argument("--version", default="")

    core_plugins_parser = subparsers.add_parser("build-manifest-core-plugins")
    core_plugins_parser.add_argument("--manifest", required=True)

    args = parser.parse_args()
    if args.command == "input-manifest-facts":
        input_manifest_facts(args.manifest)
    elif args.command == "build-manifest-core-plugins":
        build_manifest_core_plugins(args.manifest)
    else:
        bundle_manifest_components(args.manifest, args.version)


if __name__ == "__main__":
    main()
