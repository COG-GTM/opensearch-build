# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import importlib.util
import json
import os
import tempfile
import unittest
from typing import Any, Dict
from unittest.mock import patch

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, "deployment", "packer", "render_template.py")

spec = importlib.util.spec_from_file_location("render_template", SCRIPT)
render_template: Any = importlib.util.module_from_spec(spec)  # type: ignore
spec.loader.exec_module(render_template)  # type: ignore

ENV = {
    "PACKER_BUILD_VPC_ID": "vpc-1",
    "PACKER_BUILD_SUBNET_ID": "subnet-1",
    "PACKER_BUILD_SG_ID": "sg-1",
    "PACKER_BUILD_REGION": "us-west-2",
}


def template(provisioners: list, **builder: Any) -> Dict[str, Any]:
    return {
        "variables": {"build-vpc": "vpc-<>", "build-subnet": "subnet-<>", "build-secgrp": "sg-<>", "build-region": "region-<>", "aws_ami_region": "region-<>"},
        "builders": [dict(type="amazon-ebs", **builder)],
        "provisioners": provisioners,
    }


REBOOT = {"type": "shell", "inline": ["sudo reboot"], "expect_disconnect": True}
SETUP = {"type": "shell", "scripts": ["scripts/al2023/al2023-agent-setups.sh"]}


class TestPackerRenderTemplate(unittest.TestCase):
    def test_substitutes_jenkins_variables(self) -> None:
        rendered = render_template.render(template([SETUP]), "jenkins-agent-al2023-x64.json", ENV, False, "")
        self.assertEqual(
            rendered["variables"],
            {"build-vpc": "vpc-1", "build-subnet": "subnet-1", "build-secgrp": "sg-1", "build-region": "us-west-2", "aws_ami_region": "us-west-2"},
        )
        self.assertEqual(rendered["provisioners"], [SETUP])
        self.assertNotIn("post-processors", rendered)

    def test_runner_installed_before_final_reboot(self) -> None:
        rendered = render_template.render(template([SETUP, REBOOT], ssh_username="ec2-user"), "jenkins-agent-al2023-arm64.json", ENV, True, "")
        runner = rendered["provisioners"][1]
        self.assertEqual(rendered["provisioners"][-1], REBOOT)
        self.assertEqual(runner["type"], "shell")
        self.assertTrue(runner["script"].endswith("install-gha-runner.sh"))
        self.assertIn("RUNNER_PLATFORM=linux-arm64", runner["environment_vars"])
        self.assertIn("RUNNER_USER=ec2-user", runner["environment_vars"])
        self.assertIn(f"RUNNER_SHA256={render_template.RUNNER_SHA256['linux-arm64']}", runner["environment_vars"])

    def test_runner_appended_without_reboot(self) -> None:
        rendered = render_template.render(template([SETUP], ssh_username="ec2-user"), "jenkins-agent-macos14-arm64.json", ENV, True, "")
        self.assertEqual(rendered["provisioners"][0], SETUP)
        self.assertIn("RUNNER_PLATFORM=osx-arm64", rendered["provisioners"][1]["environment_vars"])

    def test_windows_runner_installed_before_sysprep(self) -> None:
        sysprep = {"type": "powershell", "inline": ["& 'C:/Program Files/Amazon/EC2Launch/EC2Launch.exe' sysprep"]}
        rendered = render_template.render(template([SETUP, sysprep], communicator="winrm"), "jenkins-agent-win2019-x64-winrm-ssh.json", ENV, True, "")
        runner = rendered["provisioners"][1]
        self.assertEqual(rendered["provisioners"][-1], sysprep)
        self.assertEqual(runner["type"], "powershell")
        self.assertTrue(runner["script"].endswith("install-gha-runner.ps1"))
        self.assertIn("RUNNER_PLATFORM=win-x64", runner["environment_vars"])

    def test_manifest_post_processor(self) -> None:
        rendered = render_template.render(template([SETUP]), "jenkins-agent-al2023-x64.json", ENV, False, "/tmp/manifest.json")
        self.assertEqual(rendered["post-processors"], [{"type": "manifest", "output": "/tmp/manifest.json", "strip_path": True}])

    def test_runner_platform(self) -> None:
        self.assertEqual(render_template.runner_platform("legacy/jenkins-agent-macos14-x64.json"), "osx-x64")
        self.assertEqual(render_template.runner_platform("jenkins-agent-ubuntu2404-x64.json"), "linux-x64")
        with self.assertRaises(ValueError):
            render_template.runner_platform("jenkins-agent-win2022-arm64.json")

    def test_substitute_path(self) -> None:
        self.assertEqual(render_template.substitute_path("jenkins-agent-al2-x64.json"), "substitute_jenkins-agent-al2-x64.json")
        self.assertEqual(render_template.substitute_path("legacy/a.json"), os.path.join("legacy", "substitute_a.json"))

    def test_main_writes_substitute(self) -> None:
        with tempfile.TemporaryDirectory() as packer_dir:
            with open(os.path.join(packer_dir, "jenkins-agent-al2023-x64.json"), "w") as f:
                json.dump(template([SETUP, REBOOT], ssh_username="ec2-user"), f)
            with patch.dict(os.environ, ENV):
                self.assertEqual(render_template.main(["jenkins-agent-al2023-x64.json", "--packer-dir", packer_dir, "--install-runner"]), 0)
            with open(os.path.join(packer_dir, "substitute_jenkins-agent-al2023-x64.json")) as f:
                rendered = json.load(f)
            self.assertEqual(rendered["variables"]["build-vpc"], "vpc-1")
            self.assertEqual(len(rendered["provisioners"]), 3)

    def test_main_rejects_bad_input(self) -> None:
        with tempfile.TemporaryDirectory() as packer_dir:
            open(os.path.join(packer_dir, "a.json"), "w").close()
            with patch.dict(os.environ, ENV):
                for name in ["", "../a.json", "/etc/a.json", "a.hcl", "missing.json"]:
                    with self.assertRaises(SystemExit, msg=name):
                        render_template.main([name, "--packer-dir", packer_dir])
            with patch.dict(os.environ, {k: "" for k in ENV}):
                with self.assertRaises(SystemExit):
                    render_template.main(["a.json", "--packer-dir", packer_dir])

    def test_runner_checksums_cover_all_platforms(self) -> None:
        for platform, digest in render_template.RUNNER_SHA256.items():
            self.assertRegex(digest, r"^[0-9a-f]{64}$", platform)
