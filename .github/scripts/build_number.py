# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Jenkins BUILD_NUMBER equivalent of a GitHub Actions run (plan decision d-build-number: offset + run_number).

The number is build.id of the build manifests, the <build> segment of the S3 keys (<job>/<version>/<build>/...), the
docker build-number tag and the *_build_number integer fields of the metrics indices, so it must fit a signed 32-bit
integer and never reuse a Jenkins build number of the same job. Jenkins was at 12335 (distribution-build-opensearch),
9131 (distribution-build-opensearch-dashboards), 11991 (integ-test), 9296 (integ-test-opensearch-dashboards) and 1947
(smoke-test) when the jobs moved, so GitHub numbers start above OFFSET:

    OFFSET + run_number + lane * LANE_SIZE + (run_attempt - 1) * ATTEMPT_STRIDE

    distribution-build-opensearch.yml run 42           -> 100042
    the same run, "Re-run all jobs" (attempt 2)        -> 10100042
    integ-test called from that run (lane 1)           -> 1100042

github.run_number counts per workflow file, and a reusable workflow sees its caller's run_number. A job's own workflow
(JOB_WORKFLOWS) uses lane 0; every other workflow that writes the same job (a distribution build calling the test
workflows, the dry runs) has its own lane (CALLER_LANES), so two counters never meet under one S3 prefix or metrics
job. A new attempt gets a new number, so a full re-run never overwrites its first attempt's keys; "Re-run failed jobs"
keeps the outputs of the job that derived the number, so all legs of one build share it.

Usage:
  build_number.py number [--job JOB] [--workflow FILE] [--run-number N] [--run-attempt N]
      defaults: the run's own workflow, GITHUB_WORKFLOW_REF, GITHUB_RUN_NUMBER, GITHUB_RUN_ATTEMPT.
      Prints build-number=<n>, and appends it to $GITHUB_OUTPUT when set.
  build_number.py build-url --job JOB --number N --repository-url URL
      The Jenkins pipeline URL of a Jenkins number, the GitHub workflow page of a GitHub number.
"""

import argparse
import os
import sys
from typing import List, Optional, Tuple

OFFSET = 100000
LANE_SIZE = 1000000
LANES = 10
ATTEMPT_STRIDE = LANE_SIZE * LANES
MAX_RUN_NUMBER = LANE_SIZE - OFFSET - 1
MAX_BUILD_NUMBER = 2**31 - 1

# Jenkins job (first segment of the S3 keys, metrics job name) -> the workflow that replaced it.
JOB_WORKFLOWS = {
    "distribution-build-opensearch": "distribution-build-opensearch.yml",
    "distribution-build-opensearch-dashboards": "distribution-build-opensearch-dashboards.yml",
    "feature-build-opensearch": "feature-build-opensearch.yml",
    "publish-opensearch-min-snapshots": "publish-min-snapshots.yml",
    "integ-test": "integ-test-opensearch.yml",
    "integ-test-opensearch-dashboards": "integ-test-opensearch-dashboards.yml",
    "bwc-test": "bwc-test-opensearch.yml",
    "bwc-test-opensearch-dashboards": "bwc-test-opensearch-dashboards.yml",
    "smoke-test": "smoke-test-opensearch.yml",
}

# Workflows that write a job they do not own (through workflow_call). Lane 0 is the owner's.
CALLER_LANES = {
    "distribution-build-opensearch.yml": 1,
    "distribution-build-opensearch-dashboards.yml": 2,
    "build-distribution-min-snapshot.yml": 3,
    "test-workflows-dry-run.yml": 4,
    "actions-smoke.yml": 5,
    "release-proof-dry-run.yml": 6,
}


def workflow_file(workflow_ref: str) -> str:
    """distribution-build-opensearch.yml from owner/repo/.github/workflows/distribution-build-opensearch.yml@refs/..."""
    return workflow_ref.split("@", 1)[0].rsplit("/", 1)[-1]


def lane(job: str, workflow: str) -> int:
    if not job or JOB_WORKFLOWS.get(job) == workflow:
        return 0
    if workflow not in CALLER_LANES:
        raise ValueError(f"{workflow} writes job {job} but has no lane in build_number.py CALLER_LANES")
    return CALLER_LANES[workflow]


def build_number(run_number: int, run_attempt: int = 1, job: str = "", workflow: str = "") -> int:
    if not 1 <= run_number <= MAX_RUN_NUMBER:
        raise ValueError(f"run_number {run_number} is out of range 1..{MAX_RUN_NUMBER}")
    if run_attempt < 1:
        raise ValueError(f"run_attempt {run_attempt} must be at least 1")
    number = OFFSET + run_number + lane(job, workflow) * LANE_SIZE + (run_attempt - 1) * ATTEMPT_STRIDE
    if number > MAX_BUILD_NUMBER:
        raise ValueError(f"run_attempt {run_attempt} gives build number {number}, above the integer limit {MAX_BUILD_NUMBER}")
    return number


def is_github_build_number(number: int) -> bool:
    return OFFSET < number <= MAX_BUILD_NUMBER and (number - OFFSET) % LANE_SIZE != 0


def parse(number: int) -> Tuple[int, int, int]:
    """(run_number, run_attempt, lane) of a GitHub build number."""
    if not is_github_build_number(number):
        raise ValueError(f"{number} is not a GitHub Actions build number")
    attempt, rest = divmod(number - OFFSET, ATTEMPT_STRIDE)
    lane_index, run_number = divmod(rest, LANE_SIZE)
    if run_number > MAX_RUN_NUMBER:
        raise ValueError(f"{number} is not a GitHub Actions build number")
    return run_number, attempt + 1, lane_index


def build_url(job: str, number: int, repository_url: str) -> str:
    if not is_github_build_number(number):
        return f"https://build.ci.opensearch.org/blue/organizations/jenkins/{job}/detail/{job}/{number}/pipeline"
    lane_index = parse(number)[2]
    callers = [name for name, index in CALLER_LANES.items() if index == lane_index]
    workflow = JOB_WORKFLOWS.get(job, f"{job}.yml") if lane_index == 0 or not callers else callers[0]
    return f"{repository_url}/actions/workflows/{workflow}"


def write_output(line: str) -> None:
    print(line)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as f:
            f.write(line + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    number_cmd = commands.add_parser("number", help="Build number of this run")
    number_cmd.add_argument("--job", default="", help="Jenkins job the number is for; empty is the run's own workflow")
    number_cmd.add_argument("--workflow", default=workflow_file(os.environ.get("GITHUB_WORKFLOW_REF", "")))
    number_cmd.add_argument("--run-number", type=int, default=int(os.environ.get("GITHUB_RUN_NUMBER") or "0"))
    number_cmd.add_argument("--run-attempt", type=int, default=int(os.environ.get("GITHUB_RUN_ATTEMPT") or "1"))
    url_cmd = commands.add_parser("build-url", help="URL of the build behind a build number")
    url_cmd.add_argument("--job", required=True)
    url_cmd.add_argument("--number", type=int, required=True)
    url_cmd.add_argument("--repository-url", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "number":
            write_output(f"build-number={build_number(args.run_number, args.run_attempt, args.job, workflow_file(args.workflow))}")
        else:
            write_output(f"build-url={build_url(args.job, args.number, args.repository_url)}")
    except ValueError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
