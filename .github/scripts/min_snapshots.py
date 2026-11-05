#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Stage the files uploadMinSnapshotsToS3 (opensearch-build-libraries jenkins@12.0.0) wrote to snapshots/.

Input is <distribution>/builds/<filename>/ of a `build.sh --snapshot --component OpenSearch` run (manifest.yml,
dist/, core-plugins/). The staging directory mirrors the S3 keys under snapshots/:

  core/<filename>/<version>/<filename>-min-<version>-<platform>-<arch>-latest.<ext>{,.sha512,.build-manifest.yml}
  plugins/<plugin>/<revision>/<every core-plugins file starting with the plugin name>   (linux x64 tar only)

The .sha512 of the -latest copy is the checksum of the min artifact with `.<ext>` rewritten to `-latest.<ext>`, the
same `sed -i "s/.<ext>/-latest.<ext>/g"` Jenkins ran (unescaped dot included). Prints key=value lines for
$GITHUB_OUTPUT.
"""

import argparse
import hashlib
import os
import re
import shutil
import sys
from typing import Any, Dict, List

import yaml


def extension(distribution: str) -> str:
    return "tar.gz" if distribution == "tar" else distribution


def sha512_line(path: str) -> str:
    digest = hashlib.sha512()
    with open(path, "rb") as artifact:
        for chunk in iter(lambda: artifact.read(1 << 20), b""):
            digest.update(chunk)
    return f"{digest.hexdigest()}  {os.path.basename(path)}\n"


def core_plugin_list(manifest: Dict[str, Any]) -> List[str]:
    # buildManifestYamlOnly.components.artifacts.'core-plugins'[0]: the first component that lists core plugins
    for component in manifest.get("components") or []:
        plugins = (component.get("artifacts") or {}).get("core-plugins")
        if plugins:
            return list(plugins)
    return []


def prepare(build_manifest_path: str, staging: str) -> Dict[str, str]:
    with open(build_manifest_path) as f:
        manifest = yaml.safe_load(f)
    build = manifest["build"]
    filename = build["name"].lower().replace(" ", "-")
    version = str(build["version"])
    platform, architecture, distribution = build["platform"], build["architecture"], build["distribution"]
    ext = extension(distribution)
    root = os.path.dirname(build_manifest_path)
    base = f"{filename}-min-{version}-{platform}-{architecture}"
    src = os.path.join(root, "dist", f"{base}.{ext}")
    if not os.path.isfile(src):
        raise FileNotFoundError(f"min artifact {src} not found")

    core = os.path.join(staging, "core", filename, version)
    os.makedirs(core, exist_ok=True)
    latest = os.path.join(core, f"{base}-latest.{ext}")
    shutil.copyfile(src, latest)
    with open(f"{latest}.sha512", "w", newline="\n") as f:
        f.write(re.sub(f".{ext}", f"-latest.{ext}", sha512_line(src)))
    shutil.copyfile(build_manifest_path, f"{latest}.build-manifest.yml")

    plugins_dir = os.path.join(root, "core-plugins")
    with_plugins = platform == "linux" and architecture == "x64" and distribution == "tar" and os.path.isdir(plugins_dir)
    plugin_count = 0
    if with_plugins:
        revision = version if version.endswith("-SNAPSHOT") else f"{version}-SNAPSHOT"
        files = sorted(os.listdir(plugins_dir))
        for sub_path in core_plugin_list(manifest):
            plugin = sub_path.split("/")[1].replace(f"-{revision}.zip", "")
            target = os.path.join(staging, "plugins", plugin, revision)
            # s3Upload(workingDir: core-plugins/, includePathPattern: "**/<plugin>*")
            for name in files:
                if name.startswith(plugin):
                    os.makedirs(target, exist_ok=True)
                    shutil.copyfile(os.path.join(plugins_dir, name), os.path.join(target, name))
            plugin_count += 1

    major = int(version.split(".")[0])
    return {
        "version": version,
        "base-name": base,
        "core-plugins": "true" if with_plugins else "false",
        "core-plugin-count": str(plugin_count),
        "signing-email": "release@opensearch.org" if major > 2 else "opensearch@amazon.com",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    stage = subparsers.add_parser("prepare", help="Stage the snapshots/ files of one build")
    stage.add_argument("--build-manifest", required=True)
    stage.add_argument("--staging", required=True)
    args = parser.parse_args()
    for key, value in prepare(args.build_manifest, args.staging).items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
