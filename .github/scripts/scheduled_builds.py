#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Schedules of the scheduled build workflows (.github/workflows/_scheduled-build.yml).

check-for-build, publish-min-snapshots and feature-build-opensearch replace Jenkins parameterizedCron entries with
.github/schedules/<workflow>.yml. Each schedule entry becomes one check-matrix row: the row's manifest is locked
(`./build.sh <manifest> --lock`), the sha1 of the lock is looked up under <job>/<version>/shas/<sha>.yml
(getManifestSHA), and only a changed manifest dispatches its build and records the SHA (buildUploadManifestSHA).

Subcommands:
  matrix        turn a schedule tick, workflow_dispatch inputs or a pull request into the check matrix
  manifest-sha  sha1 of a lock file and its getManifestSHA path
"""

import argparse
import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

import yaml

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Jenkins: `if (TARGET_JOB_NAME != 'distribution-build-opensearch' && ...) error "Job ${TARGET_JOB_NAME} is invalid"`
CHECK_FOR_BUILD_TARGETS = ("distribution-build-opensearch", "distribution-build-opensearch-dashboards")

WORKFLOWS: Dict[str, Dict[str, Any]] = {
    "check-for-build": {
        "manifests-dir": "manifests",
        "params": ["INPUT_MANIFEST", "TEST_MANIFEST", "TARGET_JOB_NAME", "BUILD_PLATFORM", "BUILD_DISTRIBUTION",
                   "TEST_PLATFORM", "TEST_DISTRIBUTION", "UPDATE_GITHUB_ISSUE", "BUILD_ARGS"],
    },
    "publish-min-snapshots": {
        "manifests-dir": "manifests",
        # Jenkins job name, the first segment of the SHA path
        "job-name": "publish-opensearch-min-snapshots",
        "params": ["INPUT_MANIFEST", "TARGETS", "PUBLISH"],
    },
    "feature-build-opensearch": {
        "manifests-dir": "feature-manifests",
        "job-name": "feature-build-opensearch",
        "params": ["INPUT_MANIFEST", "COMPONENT_NAME", "BUILD_PLATFORM", "BUILD_DISTRIBUTION", "UPDATE_LATEST_URL",
                   "CONTINUE_ON_ERROR", "SKIP_ARTIFACT_CHECK", "RUNS_ON"],
    },
}

# workflow_dispatch inputs of the check-for-build targets that BUILD_ARGS may set (OPTIONS is appended)
BUILD_ARGS_KEYS = {
    "distribution-build-opensearch": {"COMPONENT_NAME", "RC_NUMBER", "CONTINUE_ON_ERROR", "UPDATE_LATEST_URL", "RUNS_ON", "OPTIONS"},
    "distribution-build-opensearch-dashboards": {"COMPONENT_NAME", "RC_NUMBER", "BUILD_DOCKER", "OPTIONS"},
}

PATH_RE = re.compile(r"[A-Za-z0-9._/-]+\.yml")
WORD_RE = re.compile(r"[A-Za-z0-9 ._-]*")


def schedule_path(workflow: str) -> str:
    return os.path.join(ROOT, ".github", "schedules", f"{workflow}.yml")


def load_schedule(workflow: str, path: Optional[str] = None) -> List[Dict[str, Any]]:
    with open(path or schedule_path(workflow)) as f:
        return list(yaml.safe_load(f)["entries"])


def schedule_crons(workflow: str, path: Optional[str] = None) -> List[str]:
    crons: List[str] = []
    for entry in load_schedule(workflow, path):
        if entry["cron"] not in crons:
            crons.append(entry["cron"])
    return crons


def to_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() == "true"


def normalize(workflow: str, params: Dict[str, Any]) -> Dict[str, str]:
    """Trim the parameters (Jenkins `trim: true`) and reject unknown ones and unsafe values."""
    known = WORKFLOWS[workflow]["params"] + ["CHECK_FOR_CHANGES"]
    out: Dict[str, str] = {}
    for key, value in params.items():
        if key not in known:
            raise ValueError(f"unknown {workflow} parameter '{key}' (known: {', '.join(known)})")
        if value is None or key == "CHECK_FOR_CHANGES":
            continue
        out[key] = (json.dumps(value) if isinstance(value, (dict, list)) else str(value)).strip()
    manifest = out.get("INPUT_MANIFEST", "")
    manifests_dir = WORKFLOWS[workflow]["manifests-dir"]
    if not PATH_RE.fullmatch(manifest) or ".." in manifest or not os.path.isfile(os.path.join(ROOT, manifests_dir, manifest)):
        raise ValueError(f"Input manifest was not provided or not found at {manifests_dir}/{manifest}.")
    test_manifest = out.get("TEST_MANIFEST", "")
    if test_manifest and (not PATH_RE.fullmatch(test_manifest) or ".." in test_manifest
                          or not os.path.isfile(os.path.join(ROOT, "manifests", test_manifest))):
        raise ValueError(f"Test manifest not found at manifests/{test_manifest}.")
    for key in ("BUILD_PLATFORM", "BUILD_DISTRIBUTION", "TEST_PLATFORM", "TEST_DISTRIBUTION", "COMPONENT_NAME", "TARGETS"):
        if not WORD_RE.fullmatch(out.get(key, "")):
            raise ValueError(f"{key} is not valid: '{out[key]}'")
    if workflow == "check-for-build" and out.get("TARGET_JOB_NAME") not in CHECK_FOR_BUILD_TARGETS:
        raise ValueError(f"Job {out.get('TARGET_JOB_NAME')} is invalid")
    return out


def check_for_build_inputs(params: Dict[str, str]) -> Dict[str, str]:
    """The `build job: TARGET_JOB_NAME, parameters: [...]` call of check-for-build.jenkinsfile as dispatch inputs."""
    target = params["TARGET_JOB_NAME"]
    inputs = {"INPUT_MANIFEST": params["INPUT_MANIFEST"]}
    # Empty Jenkins parameters were passed through as empty strings; here they keep the target's defaults.
    for key in ("TEST_MANIFEST", "BUILD_PLATFORM", "BUILD_DISTRIBUTION"):
        if params.get(key):
            inputs[key] = params[key]
    options: List[str] = []
    update_issue = to_bool(params.get("UPDATE_GITHUB_ISSUE"), True)
    if target == "distribution-build-opensearch":
        # TEST_PLATFORM / TEST_DISTRIBUTION / UPDATE_GITHUB_ISSUE are packed into OPTIONS there
        if params.get("TEST_PLATFORM"):
            options.append("test-platform=" + ",".join(params["TEST_PLATFORM"].split()))
        if params.get("TEST_DISTRIBUTION"):
            options.append("test-distribution=" + ",".join(params["TEST_DISTRIBUTION"].split()))
        if not update_issue:
            options.append("no-update-github-issue")
    else:
        for key in ("TEST_PLATFORM", "TEST_DISTRIBUTION"):
            if params.get(key):
                inputs[key] = params[key]
        if not update_issue:
            options.append("update-github-issue=false")
    build_args = params.get("BUILD_ARGS", "")
    if build_args:
        try:
            extra = json.loads(build_args)
        except ValueError:
            extra = None
        if not isinstance(extra, dict):
            raise ValueError(f"BUILD_ARGS must be a JSON object, got '{build_args}'")
        for key, value in extra.items():
            if key not in BUILD_ARGS_KEYS[target]:
                raise ValueError(f"BUILD_ARGS key '{key}' is not an input of {target} (allowed: {', '.join(sorted(BUILD_ARGS_KEYS[target]))})")
            value = str(value).lower() if isinstance(value, bool) else str(value)
            if key == "OPTIONS":
                options.extend(value.split())
            else:
                inputs[key] = value
    if options:
        inputs["OPTIONS"] = " ".join(options)
    return inputs


def row(workflow: str, params: Dict[str, str], index: int) -> Dict[str, Any]:
    manifests_dir = WORKFLOWS[workflow]["manifests-dir"]
    if workflow == "check-for-build":
        target = params["TARGET_JOB_NAME"]
        inputs = check_for_build_inputs(params)
        job_name = target
    else:
        target = workflow
        # the dispatched run builds right away; the check already ran here
        inputs = {key: value for key, value in params.items() if value != ""}
        inputs["CHECK_FOR_CHANGES"] = "false"
        job_name = WORKFLOWS[workflow]["job-name"]
    return {
        "index": index,
        "input-manifest": params["INPUT_MANIFEST"],
        "manifest-path": f"{manifests_dir}/{params['INPUT_MANIFEST']}",
        "job-name": job_name,
        "target": f"{target}.yml",
        # run-name of the target workflow for a dispatched run, used to find the run after `gh workflow run`
        "title": f"{target} {params['INPUT_MANIFEST']}",
        "inputs": json.dumps(inputs, separators=(",", ":"), sort_keys=True),
    }


def resolve(workflow: str, event: str, schedule: str, data: Dict[str, Any], live_var: str = "",
            schedule_file: Optional[str] = None) -> Dict[str, Any]:
    if workflow not in WORKFLOWS:
        raise ValueError(f"unknown workflow '{workflow}' (known: {', '.join(WORKFLOWS)})")
    if event == "schedule":
        entries = [e["params"] for e in load_schedule(workflow, schedule_file) if e["cron"] == schedule]
        if not entries:
            raise ValueError(f"no {workflow} schedule entry has cron '{schedule}'")
        # Scheduled runs stay dry runs (check only, no dispatch) until the repository variable is 'true'.
        live = live_var == "true"
        mode = "schedule"
    elif event == "pull_request":
        entries = [e["params"] for e in load_schedule(workflow, schedule_file)]
        live = False
        mode = "pull-request dry run"
    else:
        entries = [{k: v for k, v in data.items() if k in WORKFLOWS[workflow]["params"]}]
        live = True
        mode = "inputs"
    rows = [row(workflow, normalize(workflow, params), i) for i, params in enumerate(entries)]
    return {"matrix": {"include": rows}, "live": live, "mode": mode}


def manifest_sha(lock_file: str, job_name: str) -> Dict[str, str]:
    """getManifestSHA(): sha1 of the lock file, path <job>/<build.version>/shas/<sha>.yml (InputManifest.getSHAsRoot)."""
    with open(lock_file, "rb") as f:
        content = f.read()
    sha = hashlib.sha1(content).hexdigest()
    version = str(yaml.safe_load(content)["build"]["version"])
    return {"sha": sha, "version": version, "path": f"{job_name}/{version}/shas/{sha}.yml"}


def is_active(manifest: Dict[str, Any]) -> bool:
    """A manifest is still built while any source component follows a branch (not a tag or a pinned commit)."""
    for component in manifest.get("components", []):
        ref = component.get("ref")
        if ref is None:
            continue
        ref = str(ref)
        if not (ref.startswith("tags/") or re.fullmatch(r"[0-9a-f]{40}", ref)):
            return True
    return False


def input_manifests(manifests_dir: str = "manifests") -> List[str]:
    """Input manifests (not test manifests) under <manifests_dir>/<version>/, relative to <manifests_dir>."""
    root = os.path.join(ROOT, manifests_dir)
    found = []
    for version in sorted(os.listdir(root)):
        folder = os.path.join(root, version)
        if version == "templates" or not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.endswith(".yml") and not name.endswith("-test.yml"):
                found.append(f"{version}/{name}")
    return found


def active_manifests(manifests_dir: str = "manifests") -> List[str]:
    active = []
    for relative in input_manifests(manifests_dir):
        with open(os.path.join(ROOT, manifests_dir, relative)) as f:
            if is_active(yaml.safe_load(f)):
                active.append(relative)
    return active


def write_outputs(values: Dict[str, str]) -> None:
    lines = "".join(f"{key}={value}\n" for key, value in values.items())
    sys.stdout.write(lines)
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("matrix")
    m.add_argument("--workflow", required=True, choices=sorted(WORKFLOWS))
    m.add_argument("--event", required=True)
    m.add_argument("--schedule", default="")
    m.add_argument("--data", default="{}")
    m.add_argument("--live-var", default="")
    s = sub.add_parser("manifest-sha")
    s.add_argument("--lock", required=True)
    s.add_argument("--job-name", required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "matrix":
            result = resolve(args.workflow, args.event, args.schedule, json.loads(args.data or "{}") or {}, args.live_var)
            for r in result["matrix"]["include"]:
                print(f"{r['manifest-path']} -> {r['target']} {r['inputs']}", file=sys.stderr)
            print(f"mode: {result['mode']}, live: {result['live']}", file=sys.stderr)
            write_outputs({"matrix": json.dumps(result["matrix"], separators=(",", ":")),
                           "live": "true" if result["live"] else "false"})
        else:
            write_outputs(manifest_sha(args.lock, args.job_name))
    except ValueError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
