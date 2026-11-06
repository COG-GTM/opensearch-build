# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import contextlib
import io
import itertools
import os
import tempfile
import unittest
from unittest.mock import patch

from tests.tests_github_scripts import load_script

bn = load_script("build_number")

# Highest Jenkins build numbers per job when the jobs moved (ci.opensearch.org index.json and the metrics indices).
JENKINS_MAX = {
    "distribution-build-opensearch": 12335,
    "distribution-build-opensearch-dashboards": 9131,
    "integ-test": 11991,
    "integ-test-opensearch-dashboards": 9296,
    "smoke-test": 1947,
}
REF = "COG-GTM/opensearch-build/.github/workflows/{}@refs/heads/main"


class TestBuildNumber(unittest.TestCase):
    def test_offset_plus_run_number(self) -> None:
        self.assertEqual(bn.build_number(1), 100001)
        self.assertEqual(bn.build_number(42, 1, "distribution-build-opensearch", "distribution-build-opensearch.yml"), 100042)
        self.assertEqual(bn.build_number(42, 1, "integ-test", "integ-test-opensearch.yml"), 100042)

    def test_above_every_jenkins_build_number(self) -> None:
        self.assertGreater(bn.OFFSET, max(JENKINS_MAX.values()) * 5)
        for number in JENKINS_MAX.values():
            self.assertFalse(bn.is_github_build_number(number))
            self.assertLess(number, bn.build_number(1))

    def test_fits_the_metrics_integer_fields(self) -> None:
        max_attempt = (bn.MAX_BUILD_NUMBER - bn.OFFSET - bn.MAX_RUN_NUMBER - (bn.LANES - 1) * bn.LANE_SIZE) // bn.ATTEMPT_STRIDE + 1
        self.assertGreaterEqual(max_attempt, 50)
        self.assertLessEqual(bn.build_number(bn.MAX_RUN_NUMBER, max_attempt, "integ-test", "actions-smoke.yml"), 2**31 - 1)
        for run, attempt in ((bn.MAX_RUN_NUMBER + 1, 1), (bn.MAX_RUN_NUMBER, 300), (0, 1), (-1, 1), (1, 0)):
            with self.assertRaises(ValueError):
                bn.build_number(run, attempt)

    def test_unique_across_runs_attempts_and_callers(self) -> None:
        workflows = ["integ-test-opensearch.yml"] + list(bn.CALLER_LANES)
        runs = (1, 2, 12335, bn.MAX_RUN_NUMBER)
        seen = {bn.build_number(run, attempt, "integ-test", workflow) for run, attempt, workflow in itertools.product(runs, (1, 2, 50), workflows)}
        self.assertEqual(len(seen), len(runs) * 3 * len(workflows))
        self.assertEqual(bn.build_number(42, 2, "distribution-build-opensearch", "distribution-build-opensearch.yml"), 10100042)
        self.assertEqual(bn.build_number(42, 1, "integ-test", "distribution-build-opensearch.yml"), 1100042)
        self.assertEqual(bn.build_number(42, 1, "distribution-build-opensearch", "build-distribution-min-snapshot.yml"), 3100042)

    def test_unknown_writer_of_a_job_is_an_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "CALLER_LANES"):
            bn.build_number(1, 1, "integ-test", "some-new-workflow.yml")

    def test_parse_round_trips(self) -> None:
        for run, attempt, workflow in ((1, 1, "integ-test-opensearch.yml"), (42, 2, "distribution-build-opensearch.yml"), (bn.MAX_RUN_NUMBER, 7, "actions-smoke.yml")):
            number = bn.build_number(run, attempt, "integ-test", workflow)
            self.assertEqual(bn.parse(number), (run, attempt, bn.lane("integ-test", workflow)))
        for number in (12335, bn.OFFSET, bn.OFFSET + bn.LANE_SIZE):
            with self.assertRaises(ValueError):
                bn.parse(number)

    def test_build_url(self) -> None:
        repo = "https://github.com/COG-GTM/opensearch-build"
        self.assertEqual(bn.build_url("distribution-build-opensearch", 12181, repo),
                         "https://build.ci.opensearch.org/blue/organizations/jenkins/distribution-build-opensearch/detail/distribution-build-opensearch/12181/pipeline")
        self.assertEqual(bn.build_url("distribution-build-opensearch", 100042, repo), f"{repo}/actions/workflows/distribution-build-opensearch.yml")
        self.assertEqual(bn.build_url("distribution-build-opensearch", 4100042, repo), f"{repo}/actions/workflows/test-workflows-dry-run.yml")

    def test_main_reads_the_github_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "output")
            env = {"GITHUB_RUN_NUMBER": "7", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_OUTPUT": output,
                   "GITHUB_WORKFLOW_REF": REF.format("distribution-build-opensearch.yml")}
            stdout = io.StringIO()
            with patch.dict(os.environ, env), contextlib.redirect_stdout(stdout):
                self.assertEqual(bn.main(["number", "--job", "integ-test"]), 0)
                self.assertEqual(bn.main(["number", "--job", "distribution-build-opensearch"]), 0)
            self.assertEqual(stdout.getvalue(), "build-number=11100007\nbuild-number=10100007\n")
            with open(output) as f:
                self.assertEqual(f.read(), stdout.getvalue())

    def test_main_fails_without_a_run_number(self) -> None:
        with patch.dict(os.environ, {"GITHUB_OUTPUT": "", "GITHUB_RUN_NUMBER": ""}), contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bn.main(["number"]), 1)
