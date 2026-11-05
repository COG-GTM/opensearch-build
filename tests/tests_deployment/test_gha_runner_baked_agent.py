# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import json
import os
import re
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
RUNNERS = os.path.join(ROOT, "deployment", "gha-runners")
TEMPLATES = os.path.join(RUNNERS, "templates")
RUNNER_VERSION_JSON = os.path.join(ROOT, "deployment", "packer", "gha-runner", "runner-version.json")
USERDATA_TEMPLATES = ["user-data-al2023.sh", "user-data-ubuntu.sh", "user-data-windows.ps1", "user-data-osx.sh"]


def read(path: str) -> str:
    with open(path) as f:
        return f.read()


class TestGhaRunnerBakedAgent(unittest.TestCase):
    def test_pools_use_baked_agent(self) -> None:
        runners_tf = read(os.path.join(RUNNERS, "runners.tf"))
        self.assertIn("enable_runner_binaries_syncer = false", runners_tf)
        self.assertIn("userdata_post_install         = local.baked_runner_check[p.os]", runners_tf)
        self.assertIn("runner-version.json", runners_tf)
        for name in USERDATA_TEMPLATES:
            self.assertIn(name, runners_tf)

    def test_userdata_templates_do_not_install_runner(self) -> None:
        for name in USERDATA_TEMPLATES:
            content = read(os.path.join(TEMPLATES, name))
            self.assertNotIn("${install_runner}", content, name)
            self.assertEqual(content.count("${post_install}"), 1, name)
            self.assertEqual(content.count("${start_runner}"), 1, name)
            self.assertLess(content.index("${post_install}"), content.index("${start_runner}"), name)

    def test_version_check_templates_take_packer_pin(self) -> None:
        for ext in ["sh", "ps1"]:
            content = read(os.path.join(TEMPLATES, f"baked-runner.{ext}.tftpl"))
            self.assertEqual(re.findall(r"(?<!\$)\$\{([^}]+)\}", content), ["min_runner_version"], ext)
        self.assertRegex(json.loads(read(RUNNER_VERSION_JSON))["version"], r"^\d+\.\d+\.\d+$")

    def test_start_runner_runs_from_agent_directory(self) -> None:
        # The module's start scripts call ./config.sh / ./run.sh (.\\run.cmd); install_runner used to cd there.
        sh = read(os.path.join(TEMPLATES, "baked-runner.sh.tftpl")).rstrip()
        ps1 = read(os.path.join(TEMPLATES, "baked-runner.ps1.tftpl")).rstrip()
        self.assertTrue(sh.endswith('cd "$runner_dir"'))
        self.assertTrue(ps1.endswith("Set-Location C:\\actions-runner"))


@unittest.skipIf(os.name == "nt", "the Linux/macOS user-data check needs a POSIX shell")
class TestBakedRunnerShellCheck(unittest.TestCase):
    MIN_VERSION = "2.338.2"

    def run_check(self, baked_version: str = None) -> subprocess.CompletedProcess:
        tmp = tempfile.mkdtemp()
        runner_dir = os.path.join(tmp, "actions-runner")
        bin_dir = os.path.join(tmp, "stubs")
        os.makedirs(os.path.join(runner_dir, "bin"))
        os.makedirs(bin_dir)

        def stub(path: str, body: str) -> None:
            with open(path, "w") as f:
                f.write(f"#!/bin/bash\n{body}\n")
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)

        if baked_version is not None:
            stub(os.path.join(runner_dir, "bin", "Runner.Listener"), f"echo {baked_version}")
        calls = os.path.join(tmp, "calls")
        for command in ["aws", "shutdown", "logger", "chown", "chmod", "amazon-cloudwatch-agent-ctl", "sleep"]:
            stub(os.path.join(bin_dir, command), f'echo "{command} $*" >> {calls}')
        stub(os.path.join(bin_dir, "curl"), 'case "$*" in *instance-id*) echo i-0123;; *region*) echo us-east-1;; *ssm_config_path*) echo /cfg;; *) echo token;; esac')

        script = read(os.path.join(TEMPLATES, "baked-runner.sh.tftpl"))
        script = script.replace("${min_runner_version}", self.MIN_VERSION)
        script = script.replace("/opt/actions-runner", runner_dir).replace("/opt/hostedtoolcache", os.path.join(tmp, "toolcache"))
        script = script.replace("/Users/runner", os.path.join(tmp, "runner-home"))
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
        result = subprocess.run(["bash", "-e", "-c", f"user_name=ec2-user\ncd /\n{script}\necho START_RUNNER in $PWD"],
                                capture_output=True, text=True, env=env)
        result.calls = read(calls) if os.path.exists(calls) else ""  # type: ignore
        result.runner_dir = runner_dir  # type: ignore
        return result

    def test_current_agent_starts(self) -> None:
        result = self.run_check(self.MIN_VERSION)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Using baked actions/runner {self.MIN_VERSION}", result.stdout)
        self.assertIn(f"START_RUNNER in {result.runner_dir}", result.stdout)  # type: ignore
        self.assertNotIn("terminate-instances", result.calls)  # type: ignore

    def test_newer_agent_starts(self) -> None:
        result = self.run_check("2.340.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("START_RUNNER", result.stdout)

    def assert_fails_loudly(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 1)
        self.assertIn("FATAL: stale runner AMI", result.stderr)
        self.assertNotIn("START_RUNNER", result.stdout)
        self.assertIn("aws ec2 terminate-instances --instance-ids i-0123 --region us-east-1", result.calls)  # type: ignore
        self.assertIn("ssm:/cfg/cloudwatch_agent_config_runner", result.calls)  # type: ignore

    def test_older_agent_fails(self) -> None:
        for version in ["2.338.1", "2.337.9", "1.999.999"]:
            with self.subTest(version=version):
                self.assert_fails_loudly(self.run_check(version))

    def test_missing_agent_fails(self) -> None:
        self.assert_fails_loudly(self.run_check(None))

    def test_unreadable_version_fails(self) -> None:
        self.assert_fails_loudly(self.run_check("not-a-version"))
