#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Render an opensearch-ci packer template for .github/workflows/packer-build.yml.

Does what jenkins/packer/packer-build-lf.jenkinsfile does with readJSON/writeJSON (fill the build
VPC/subnet/security group/region variables), then optionally adds the GitHub Actions runner agent
(ADR 0001: the runner agent is the only addition to the Jenkins AMIs) and a manifest post-processor
so the workflow can report the AMI ID.
"""

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List

# Baked agent version. While the runner pools keep the module's boot-time install_runner, that copy
# replaces this one at launch (see README.md).
RUNNER_VERSION = "2.338.2"
# SHA-256 of actions-runner-<os>-<arch>-<version>.(tar.gz|zip), from the actions/runner release notes.
RUNNER_SHA256 = {
    "linux-x64": "baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40",
    "linux-arm64": "670adb2f268fe21dcbb58b3db0e0aa9d5b3bddfe2c651481336e561ff86de945",
    "osx-x64": "109fff505a34014a7e8d1acb850c33d404811102f572b8fb9788e2fff013cc49",
    "osx-arm64": "25a065af409ffa317ab4d5a3b614dd15fc9c40f2172bc1b35a1b1258e919a388",
    "win-x64": "a1f17aa987fc8158bc7521d284912267a0d01b7c2c4684b33c86783b5e8221cc",
}

TEMPLATE_NAME = re.compile(r"^(legacy/)?[A-Za-z0-9][A-Za-z0-9._-]*\.json$")

# template variable -> environment variable (ADR 0001 secret names)
BUILD_VARIABLES = {
    "build-vpc": "PACKER_BUILD_VPC_ID",
    "build-subnet": "PACKER_BUILD_SUBNET_ID",
    "build-secgrp": "PACKER_BUILD_SG_ID",
    "build-region": "PACKER_BUILD_REGION",
    "aws_ami_region": "PACKER_BUILD_REGION",
}

SCRIPT_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "gha-runner")


def runner_platform(template_name: str) -> str:
    name = os.path.basename(template_name).lower()
    system = "win" if "win" in name else "osx" if "macos" in name else "linux"
    arch = "arm64" if "arm64" in name else "x64"
    platform = f"{system}-{arch}"
    if platform not in RUNNER_SHA256:
        raise ValueError(f"No runner agent build for {platform} ({template_name})")
    return platform


def runner_provisioner(platform: str, user: str) -> Dict[str, Any]:
    env = [f"RUNNER_VERSION={RUNNER_VERSION}", f"RUNNER_PLATFORM={platform}", f"RUNNER_SHA256={RUNNER_SHA256[platform]}"]
    if platform.startswith("win"):
        return {"type": "powershell", "script": os.path.join(SCRIPT_DIR, "install-gha-runner.ps1"), "environment_vars": env}
    return {
        "type": "shell",
        "script": os.path.join(SCRIPT_DIR, "install-gha-runner.sh"),
        "environment_vars": env + [f"RUNNER_USER={user}"],
        "execute_command": "chmod +x {{ .Path }}; sudo {{ .Vars }} bash {{ .Path }}",
    }


def add_runner_agent(template: Dict[str, Any], template_name: str) -> None:
    platform = runner_platform(template_name)
    builder = template["builders"][0]
    provisioner = runner_provisioner(platform, builder.get("ssh_username", "ec2-user"))
    provisioners: List[Dict[str, Any]] = template.setdefault("provisioners", [])
    # Install before the final reboot (Linux) or EC2Launch sysprep (Windows) so it lands in the image.
    last = provisioners[-1] if provisioners else {}
    if platform.startswith("win") or last.get("expect_disconnect"):
        provisioners.insert(len(provisioners) - 1, provisioner)
    else:
        provisioners.append(provisioner)


def render(template: Dict[str, Any], template_name: str, values: Dict[str, str], install_runner: bool, manifest: str) -> Dict[str, Any]:
    variables = template.setdefault("variables", {})
    for variable, env_name in BUILD_VARIABLES.items():
        variables[variable] = values[env_name]
    if install_runner:
        add_runner_agent(template, template_name)
    if manifest:
        template.setdefault("post-processors", []).append({"type": "manifest", "output": manifest, "strip_path": True})
    return template


def substitute_path(template_name: str) -> str:
    head, tail = os.path.split(template_name)
    return os.path.join(head, f"substitute_{tail}")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template_name", help="template path relative to the opensearch-ci packer/ directory")
    parser.add_argument("--packer-dir", required=True, help="opensearch-ci packer/ directory")
    parser.add_argument("--install-runner", action="store_true", help="add the GitHub Actions runner agent")
    parser.add_argument("--manifest", default="", help="write a packer manifest post-processor output to this path")
    args = parser.parse_args(argv)

    if not TEMPLATE_NAME.match(args.template_name):
        parser.error(f"invalid template name {args.template_name!r}: expected <name>.json or legacy/<name>.json")
    source = os.path.join(args.packer_dir, args.template_name)
    if not os.path.isfile(source):
        parser.error(f"{args.template_name} does not exist in {args.packer_dir}")
    missing = sorted({env for env in BUILD_VARIABLES.values() if not os.environ.get(env)})
    if missing:
        parser.error(f"missing environment variables: {', '.join(missing)}")

    with open(source) as f:
        template = json.load(f)
    values = {env: os.environ[env] for env in BUILD_VARIABLES.values()}
    rendered = render(template, args.template_name, values, args.install_runner, args.manifest)

    target = substitute_path(args.template_name)
    with open(os.path.join(args.packer_dir, target), "w") as f:
        json.dump(rendered, f, indent=2)
        f.write("\n")
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
