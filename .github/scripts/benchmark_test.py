#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.
"""Helpers for the benchmark-* GitHub Actions workflows (ports of jenkins/opensearch/benchmark-*.jenkinsfile).

Subcommands:
  resolve       turn a schedule, workflow_dispatch inputs or a repository_dispatch payload into the run matrix
  run           build the runBenchmarkTestScript() command line and run ./test.sh benchmark-test
  credentials   username/password of a secured CLUSTER_ENDPOINT from the BENCHMARK_ENDPOINT_CREDENTIALS secret
  edit-config   editBenchmarkConfig(): put the metrics datastore credentials into benchmark.ini
  compare-ids   getCompareBenchmarkIds(): latest baseline and contender test-execution-ids from the datastore
  comment       render the pull request comment Jenkins posted with `gh pr comment`
  webhook       publishNotification() text for the benchmark-test success/failure post
  stacks        CloudFormation stacks the Jenkins `aborted` block deleted for one run
  import-jenkins  regenerate .github/schedules/<workflow>.yml from the parameterizedCron blocks
"""
import argparse
import base64
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

BENCHMARK_LABELS = ["self-hosted", "linux", "x64", "al2023-x64-m5-2xlarge-benchmark-test"]
VECTOR_BENCHMARK_LABELS = ["self-hosted", "linux", "x64", "al2023-x64-m5-2xlarge-vector-benchmark-test"]

# Jenkins parameter names per pipeline. A parameterizedCron entry or dispatch payload key outside this list
# is rejected (Jenkins silently ignored it, which hid typos in schedules).
COMMON_CLUSTER_PARAMS = [
    "DISTRIBUTION_URL", "DISTRIBUTION_VERSION", "TEST_WORKLOAD", "SINGLE_NODE_CLUSTER", "MIN_DISTRIBUTION",
    "MANAGER_NODE_COUNT", "DATA_NODE_COUNT", "CLIENT_NODE_COUNT", "INGEST_NODE_COUNT", "ML_NODE_COUNT",
    "DATA_INSTANCE_TYPE", "DATA_NODE_STORAGE", "ML_NODE_STORAGE", "JVM_SYS_PROPS", "ADDITIONAL_CONFIG", "USER_TAGS",
    "WORKLOAD_PARAMS", "TEST_PROCEDURE", "EXCLUDE_TASKS", "INCLUDE_TASKS", "CAPTURE_NODE_STAT", "TELEMETRY_PARAMS",
]
SUITES: Dict[str, Dict[str, Any]] = {
    "benchmark-test": {
        "jenkinsfile": "jenkins/opensearch/benchmark-test.jenkinsfile",
        "workflow": "benchmark-test",
        "minute": 7,
        "runs-on": BENCHMARK_LABELS,
        "params": COMMON_CLUSTER_PARAMS + [
            "BUNDLE_MANIFEST_URL", "PLUGIN_URL", "ENABLE_REMOTE_STORE", "HEAP_SIZE_IN_GB",
            "ENABLE_INSTANCE_BASED_STORAGE", "CAPTURE_SEGMENT_REPLICATION_STAT", "PRESERVE_CLUSTER", "TEST_MODE",
        ],
    },
    "benchmark-test-vectorsearch": {
        "jenkinsfile": "jenkins/opensearch/benchmark-test-vectorsearch.jenkinsfile",
        "workflow": "benchmark-test",
        "minute": 17,
        "runs-on": VECTOR_BENCHMARK_LABELS,
        "params": COMMON_CLUSTER_PARAMS + [
            "BUNDLE_MANIFEST_URL", "ENABLE_REMOTE_STORE", "ENABLE_INSTANCE_BASED_STORAGE",
            "CAPTURE_SEGMENT_REPLICATION_STAT", "TEST_MODE",
        ],
    },
    "benchmark-test-datafusion": {
        "jenkinsfile": "jenkins/opensearch/benchmark-test-datafusion.jenkinsfile",
        "workflow": "benchmark-test",
        "minute": 27,
        "runs-on": BENCHMARK_LABELS,
        "params": COMMON_CLUSTER_PARAMS + [
            "BUNDLE_MANIFEST_URL", "PLUGIN_URL", "ENABLE_REMOTE_STORE", "HEAP_SIZE_IN_GB",
            "ENABLE_INSTANCE_BASED_STORAGE", "CAPTURE_SEGMENT_REPLICATION_STAT", "PRESERVE_CLUSTER", "TEST_MODE",
        ],
    },
    "benchmark-test-endpoint": {
        "jenkinsfile": "jenkins/opensearch/benchmark-test-endpoint.jenkinsfile",
        "workflow": "benchmark-test-endpoint",
        "minute": 37,
        "runs-on": BENCHMARK_LABELS,
        "params": [
            "CLUSTER_ENDPOINT", "SECURITY_ENABLED", "SIGV4", "REGION", "SERVICE", "TEST_WORKLOAD", "USER_TAGS",
            "WORKLOAD_PARAMS", "TEST_PROCEDURE", "EXCLUDE_TASKS", "INCLUDE_TASKS", "CAPTURE_NODE_STAT",
            "CAPTURE_SEGMENT_REPLICATION_STAT", "TELEMETRY_PARAMS", "CREDENTIALS_KEY", "TEST_MODE",
        ],
    },
    "benchmark-pull-request": {
        "jenkinsfile": "jenkins/opensearch/benchmark-pull-request.jenkinsfile",
        "workflow": "benchmark-pull-request",
        "minute": 47,
        "runs-on": BENCHMARK_LABELS,
        "params": COMMON_CLUSTER_PARAMS + ["PLUGIN_URL", "SECURITY_ENABLED", "HEAP_SIZE_IN_GB", "PRESERVE_CLUSTER", "TEST_MODE"],
    },
}
WORKFLOW_SUITES: Dict[str, List[str]] = {}
for _suite, _spec in SUITES.items():
    WORKFLOW_SUITES.setdefault(_spec["workflow"], []).append(_suite)

# Jenkins booleanParam defaults; everything else defaults to empty.
DEFAULTS: Dict[str, Dict[str, str]] = {
    "benchmark-test": {"SINGLE_NODE_CLUSTER": "true", "TEST_WORKLOAD": "nyc_taxis"},
    "benchmark-test-endpoint": {"TEST_WORKLOAD": "nyc_taxis", "REGION": "us-east-1"},
    "benchmark-pull-request": {"SINGLE_NODE_CLUSTER": "true", "TEST_WORKLOAD": "nyc_taxis"},
}
BOOLEAN_PARAMS = {
    "SINGLE_NODE_CLUSTER", "MIN_DISTRIBUTION", "ENABLE_REMOTE_STORE", "ENABLE_INSTANCE_BASED_STORAGE",
    "CAPTURE_NODE_STAT", "CAPTURE_SEGMENT_REPLICATION_STAT", "PRESERVE_CLUSTER", "SECURITY_ENABLED", "SIGV4",
    "TEST_MODE",
}
# GenericTrigger genericVariables that are not job parameters.
PAYLOAD_FIELDS = ["pull_request_number", "repository", "baseline_cluster_config"]
# Characters that would let a value escape the single-quoted / unquoted shell words the benchmark tooling builds
# (BenchmarkTestSuiteExecute runs `docker run ... --workload-params '<value>'` through a shell).
UNSAFE_VALUE = re.compile(r"['`$\\;|&<>\r\n]")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
CREDENTIALS_KEY = re.compile(r"^[A-Za-z0-9_.-]+$")
MAX_MATRIX = 256


def schedule_path(workflow: str) -> str:
    return os.path.join(ROOT, ".github", "schedules", f"{workflow}.yml")


def to_bool(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def normalize(suite: str, params: Dict[str, Any], strict: bool = True) -> Dict[str, str]:
    allowed = SUITES[suite]["params"]
    result = dict(DEFAULTS.get(SUITES[suite]["workflow"], {}))
    for key, value in params.items():
        if value is None:
            continue
        if key not in allowed:
            if strict:
                raise ValueError(f"{suite}: unknown parameter '{key}' (known: {', '.join(allowed)})")
            continue
        text = str(value).strip()
        if isinstance(value, bool):
            text = "true" if value else "false"
        if UNSAFE_VALUE.search(text):
            raise ValueError(f"{suite}: parameter '{key}' contains a character that is not allowed: {text!r}")
        if key in BOOLEAN_PARAMS:
            text = "true" if to_bool(text) else "false"
        result[key] = text
    return {k: v for k, v in result.items() if v != ""}


def parse_options(text: str) -> Dict[str, str]:
    """OPTIONS input: one KEY=VALUE per line (or separated by ';' like a parameterizedCron entry)."""
    options: Dict[str, str] = {}
    for item in re.split(r"[;\n]", text or ""):
        item = item.strip()
        if not item or item.startswith("#"):
            continue
        if "=" not in item:
            raise ValueError(f"OPTIONS entry '{item}' is not KEY=VALUE")
        key, value = item.split("=", 1)
        options[key.strip()] = value.strip()
    return options


def load_schedule(workflow: str, path: Optional[str] = None) -> List[Dict[str, Any]]:
    with open(path or schedule_path(workflow)) as f:
        data = yaml.safe_load(f) or {}
    return list(data.get("entries", []))


def schedule_crons(workflow: str, path: Optional[str] = None) -> List[str]:
    crons: List[str] = []
    for entry in load_schedule(workflow, path):
        if entry["cron"] not in crons:
            crons.append(entry["cron"])
    return crons


def read_manifest(url: str) -> Dict[str, Any]:
    if not url.startswith("https://"):
        raise ValueError(f"BUNDLE_MANIFEST_URL must be an https:// URL: {url}")
    with urllib.request.urlopen(url, timeout=60) as response:  # nosec B310 - https only
        manifest: Dict[str, Any] = yaml.safe_load(response.read())
        return manifest


def manifest_info(manifest: Dict[str, Any]) -> Dict[str, Any]:
    components = manifest.get("components") or []
    names = [c.get("name") for c in components] if isinstance(components, list) else list(components)
    commit = ""
    for c in components if isinstance(components, list) else []:
        if c.get("name") == "OpenSearch":
            commit = c.get("commit_id", "")
    build = manifest.get("build", {})
    return {
        "has-security": "security" in names,
        "build-id": str(build.get("id", "")),
        "architecture": build.get("architecture", "x64"),
        "commit-id": commit,
    }


def join_tags(*tags: str) -> str:
    return ",".join(t for t in tags if t)


def expand(suite: str, params: Dict[str, str], index: int, run_id: str, run_attempt: str,
           manifest_reader: Any = read_manifest) -> List[Dict[str, Any]]:
    """Matrix rows for one Jenkins build: one row per security variant, with the stack suffix Jenkins used."""
    workflow = SUITES[suite]["workflow"]
    base = f"{run_id}-{index}" if str(run_attempt) in ("", "1") else f"{run_id}-{run_attempt}-{index}"
    workload = params.get("TEST_WORKLOAD", "")
    rows: List[Dict[str, Any]] = []

    def row(variant: str, insecure: bool, suffix: str, user_tag: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        return {
            "name": f"{suite}-{index}-{workload}{variant}",
            "suite": suite,
            "params": json.dumps(dict(params, **(extra or {})), sort_keys=True),
            "insecure": insecure,
            "suffix": suffix,
            "user-tag": user_tag,
            "runs-on": json.dumps(SUITES[suite]["runs-on"]),
        }

    if workflow == "benchmark-test":
        if not params.get("BUNDLE_MANIFEST_URL") and not params.get("DISTRIBUTION_URL"):
            raise ValueError("Benchmark Tests failed to start. Provide either BUNDLE_MANIFEST_URL or DISTRIBUTION_URL with DISTRIBUTION_VERSION")
        has_security, build_tags, extra = False, "", {}
        if params.get("BUNDLE_MANIFEST_URL"):
            info = manifest_info(manifest_reader(params["BUNDLE_MANIFEST_URL"]))
            has_security = info["has-security"]
            # runBenchmarkTestScript getMetadataTags()
            build_tags = f"distribution-build-id:{info['build-id']},arch:{info['architecture']},os-commit-id:{info['commit-id']}"
            extra = {"BUILD_ID": info["build-id"], "ARCHITECTURE": info["architecture"]}
        user = params.get("USER_TAGS", "")
        if has_security:
            rows.append(row("-secure", False, f"{base}-secure",
                            join_tags(build_tags, user, f"security-enabled:true,jenkins-build-id:{base}"), extra))
        rows.append(row("", True, base, join_tags(build_tags, user, f"security-enabled:false,jenkins-build-id:{base}"), extra))
    elif workflow == "benchmark-pull-request":
        if not params.get("DISTRIBUTION_URL") or not params.get("DISTRIBUTION_VERSION"):
            raise ValueError("Benchmark Tests failed to start. Provide DISTRIBUTION_URL and DISTRIBUTION_VERSION to run tests")
        security = params.get("SECURITY_ENABLED", "false")
        rows.append(row("", security != "true", base, join_tags(params.get("USER_TAGS", ""), f"security-enabled:{security}")))
    else:
        if not params.get("CLUSTER_ENDPOINT"):
            raise ValueError("Benchmark Tests failed to start. Provide CLUSTER_ENDPOINT to run tests")
        key = params.get("CREDENTIALS_KEY", "")
        if key and not CREDENTIALS_KEY.match(key):
            raise ValueError(f"CREDENTIALS_KEY must be a key of BENCHMARK_ENDPOINT_CREDENTIALS, got '{key}'")
        security = params.get("SECURITY_ENABLED", "false")
        rows.append(row("", security != "true", base, join_tags(params.get("USER_TAGS", ""), f"security-enabled:{security}")))
    return rows


def resolve(workflow: str, mode: str, data: Dict[str, Any], schedule: str, run_id: str, run_attempt: str,
            suite: str = "", schedule_file: Optional[str] = None, manifest_reader: Any = read_manifest) -> Dict[str, Any]:
    builds: List[Tuple[str, Dict[str, str]]] = []
    pull_request: Dict[str, str] = {}
    if mode == "schedule":
        for entry in load_schedule(workflow, schedule_file):
            if entry["cron"] == schedule:
                builds.append((entry["suite"], normalize(entry["suite"], entry.get("params", {}))))
        if not builds:
            raise ValueError(f"no {workflow} schedule entry has cron '{schedule}'")
    elif mode == "payload":
        # GenericTrigger: payload fields map 1:1 to job parameters plus the PR fields
        # GitHub allows 10 top-level client_payload properties, so extra parameters ride in OPTIONS (KEY=VALUE;...)
        payload = {k: v for k, v in data.items() if k not in PAYLOAD_FIELDS and k not in ("dry_run", "OPTIONS")}
        payload.update(parse_options(str(data.get("OPTIONS") or "")))
        suite = suite or WORKFLOW_SUITES[workflow][0]
        builds.append((suite, normalize(suite, payload)))
        number = str(data.get("pull_request_number") or "").strip()
        repository = str(data.get("repository") or "").strip()
        baseline = str(data.get("baseline_cluster_config") or "").strip()
        if number and not number.isdigit():
            raise ValueError(f"pull_request_number must be a number, got '{number}'")
        if repository and not REPOSITORY.match(repository):
            raise ValueError(f"repository must be owner/repo, got '{repository}'")
        if baseline and UNSAFE_VALUE.search(baseline):
            raise ValueError("baseline_cluster_config contains a character that is not allowed")
        pull_request = {"pull-request-number": number, "repository": repository, "baseline-cluster-config": baseline,
                        "dry-run": "true" if to_bool(data.get("dry_run", False)) else "false"}
    elif mode == "inputs":
        suite = suite or str(data.get("SUITE") or "") or WORKFLOW_SUITES[workflow][0]
        if suite not in WORKFLOW_SUITES[workflow]:
            raise ValueError(f"unknown suite '{suite}' for {workflow} (known: {', '.join(WORKFLOW_SUITES[workflow])})")
        params = {k: v for k, v in data.items() if k not in ("SUITE", "OPTIONS")}
        params.update(parse_options(str(data.get("OPTIONS") or "")))
        builds.append((suite, normalize(suite, params)))
    else:
        raise ValueError(f"unknown mode '{mode}'")

    rows: List[Dict[str, Any]] = []
    for index, (build_suite, params) in enumerate(builds, start=1):
        rows.extend(expand(build_suite, params, index, run_id, run_attempt, manifest_reader))
    if len(rows) > MAX_MATRIX:
        raise ValueError(f"{len(rows)} matrix rows exceed the GitHub limit of {MAX_MATRIX}")
    result: Dict[str, Any] = {"matrix": {"include": rows}}
    result.update(pull_request)
    return result


def test_args(params: Dict[str, str], suffix: str, insecure: bool, user_tag: str, config: str = "",
              benchmark_config: str = "", bundle_manifest: str = "", endpoint: str = "") -> List[str]:
    """runBenchmarkTestScript(command: 'execute-test') as an argv list (no shell)."""
    p = params

    def opt(flag: str, key: str) -> List[str]:
        return [flag, p[key]] if p.get(key) else []

    def flag(name: str, key: str) -> List[str]:
        return [name] if to_bool(p.get(key, "false")) else []

    args = ["benchmark-test", "execute-test"]
    args += ["--bundle-manifest", bundle_manifest] if bundle_manifest else []
    args += opt("--distribution-url", "DISTRIBUTION_URL")
    args += opt("--plugin-url", "PLUGIN_URL")
    args += opt("--distribution-version", "DISTRIBUTION_VERSION")
    endpoint = endpoint or p.get("CLUSTER_ENDPOINT", "")
    args += ["--cluster-endpoint", endpoint] if endpoint else ["--config", config]
    args += ["--workload", p.get("TEST_WORKLOAD", "nyc_taxis")]
    args += ["--benchmark-config", benchmark_config] if benchmark_config else []
    args += ["--user-tag", user_tag] if user_tag else []
    args += ["--without-security"] if insecure else []
    args += flag("--sigv4", "SIGV4")
    args += opt("--region", "REGION") if to_bool(p.get("SIGV4", "false")) or p.get("CLUSTER_ENDPOINT") else []
    args += opt("--service", "SERVICE")
    args += flag("--single-node", "SINGLE_NODE_CLUSTER")
    args += flag("--min-distribution", "MIN_DISTRIBUTION")
    args += opt("--heap-size-in-gb", "HEAP_SIZE_IN_GB")
    args += flag("--enable-remote-store", "ENABLE_REMOTE_STORE")
    args += flag("--capture-node-stat", "CAPTURE_NODE_STAT")
    args += flag("--enable-instance-storage", "ENABLE_INSTANCE_BASED_STORAGE")
    args += flag("--capture-segment-replication-stat", "CAPTURE_SEGMENT_REPLICATION_STAT")
    args += ["--suffix", suffix] if suffix else []
    args += opt("--manager-node-count", "MANAGER_NODE_COUNT")
    args += opt("--data-node-count", "DATA_NODE_COUNT")
    args += opt("--client-node-count", "CLIENT_NODE_COUNT")
    args += opt("--ingest-node-count", "INGEST_NODE_COUNT")
    args += opt("--ml-node-count", "ML_NODE_COUNT")
    args += opt("--data-instance-type", "DATA_INSTANCE_TYPE")
    args += opt("--workload-params", "WORKLOAD_PARAMS")
    args += opt("--test-procedure", "TEST_PROCEDURE")
    args += opt("--exclude-tasks", "EXCLUDE_TASKS")
    args += opt("--include-tasks", "INCLUDE_TASKS")
    # Jenkins passed the value unquoted, so the shell split it into one argument per key:value pair
    args += ["--additional-config"] + p["ADDITIONAL_CONFIG"].split() if p.get("ADDITIONAL_CONFIG") else []
    args += opt("--data-node-storage", "DATA_NODE_STORAGE")
    args += opt("--ml-node-storage", "ML_NODE_STORAGE")
    args += opt("--jvm-sys-props", "JVM_SYS_PROPS")
    args += opt("--telemetry-params", "TELEMETRY_PARAMS")
    args += flag("--preserve-cluster", "PRESERVE_CLUSTER")
    args += flag("--test-mode", "TEST_MODE")
    return args


def compare_args(baseline: str, contender: str, suffix: str, benchmark_config: str = "") -> List[str]:
    """runBenchmarkTestScript(command: 'compare')."""
    for value in (baseline, contender):
        if not re.match(r"^[A-Za-z0-9-]+$", value or ""):
            raise ValueError(f"test-execution-id is not valid: '{value}'")
    args = ["benchmark-test", "compare", baseline, contender]
    args += ["--benchmark-config", benchmark_config] if benchmark_config else []
    args += ["--suffix", suffix] if suffix else []
    return args


def edit_config(path: str, user: str, password: str) -> None:
    with open(path) as f:
        contents = f.read()
    contents = contents.replace("insert_user_here", user).replace("insert_password_here", password)
    with open(path, "w") as f:
        f.write(contents)


def compare_queries(baseline_cluster_config: str, distribution_version: str, workload: str, pull_request_number: str) -> Dict[str, Dict[str, Any]]:
    """The two _search bodies getCompareBenchmarkIds.groovy sends."""
    def query(terms: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "size": 1,
            "query": {"bool": {"must": terms}},
            "sort": [{"test-execution-timestamp": {"order": "desc"}}],
            "_source": ["test-execution-id"],
        }

    return {
        "baseline": query([
            {"term": {"user-tags.cluster-config": baseline_cluster_config}},
            {"term": {"workload": workload}},
            {"term": {"distribution-version": distribution_version}},
            {"range": {"test-execution-timestamp": {"gte": "now-5d/d", "lte": "now/d"}}},
        ]),
        "contender": query([
            {"term": {"user-tags.pull_request_number": pull_request_number}},
            {"term": {"workload": workload}},
        ]),
    }


def latest_execution_id(response: Dict[str, Any]) -> str:
    hits = response.get("hits", {})
    if hits.get("total", {}).get("value", 0) > 0:
        return str(hits["hits"][0]["_source"]["test-execution-id"])
    return ""


def search(endpoint: str, user: str, password: str, body: Dict[str, Any]) -> Dict[str, Any]:
    context = ssl.create_default_context()
    # curl -k in getCompareBenchmarkIds: the datastore NLB serves a self-signed certificate
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(
        f"https://{endpoint}/benchmark-results-*/_search", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()})
    with urllib.request.urlopen(request, timeout=60, context=context) as response:  # nosec B310
        result: Dict[str, Any] = json.loads(response.read())
        return result


def render_comment(title: str, run_url: str, results: str) -> str:
    """The `echo ... > temp_result_N.md` block of benchmark-pull-request / benchmark-compare."""
    return f"<details><summary>{title}</summary>\n\n#### Benchmark Results for Job: {run_url}\n{results}</details>\n"


def failure_comment(run_url: str) -> str:
    return f"The benchmark job {run_url} failed.\n Please see logs to debug."


def stack_names(suffix: str, build_id: str = "", architecture: str = "") -> List[str]:
    """BenchmarkCreateCluster.stack_name for one run (the Jenkins `aborted` block listed every variant)."""
    if build_id:
        return [f"opensearch-infra-stack-{suffix}-{build_id}-{architecture}"]
    return [f"opensearch-infra-stack-{suffix}"]


def import_jenkins(workflow: str) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    for suite in WORKFLOW_SUITES[workflow]:
        spec = SUITES[suite]
        with open(os.path.join(ROOT, spec["jenkinsfile"])) as f:
            text = f.read()
        # only parameterizedCron entries contain `<cron> %KEY=VALUE;...`; commented-out (#, //) lines are skipped
        lines: Iterable[Tuple[str, str]] = re.findall(r"^\s*([0-9H][^\n%]*?)\s*%([^\n]+)$", text, re.M)
        for spec_cron, raw in lines:
            fields = spec_cron.split()
            if len(fields) != 5:
                raise ValueError(f"{suite}: unexpected cron '{spec_cron}'")
            minute = str(spec["minute"]) if fields[0] == "H" else fields[0]
            hours = fields[1].replace("H/", "*/")
            cron = " ".join([minute, hours] + fields[2:])
            params: Dict[str, str] = {}
            for item in raw.strip().split(";"):
                if item.strip():
                    key, value = item.split("=", 1)
                    params[key.strip()] = value.strip()
            normalize(suite, params)
            entries.append({"cron": cron, "suite": suite, "params": params})
    return {"entries": entries}


class IndentedDumper(yaml.SafeDumper):
    """Indent block sequences under their key, as the repository yamllint config requires."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, False)


def write_outputs(values: Dict[str, Any]) -> None:
    lines = []
    for key, value in values.items():
        text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
        lines.append(f"{key}={text}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as f:
            f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def write_github_env(values: Dict[str, str]) -> None:
    path = os.environ["GITHUB_ENV"]
    with open(path, "a") as f:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ValueError(f"{key} must be a single line")
            f.write(f"{key}={value}\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("resolve")
    p.add_argument("--workflow", required=True, choices=sorted(WORKFLOW_SUITES))
    p.add_argument("--mode", required=True, choices=["schedule", "inputs", "payload"])
    p.add_argument("--data", default="{}", help="JSON: workflow inputs or repository_dispatch client_payload")
    p.add_argument("--schedule", default="")
    p.add_argument("--suite", default="")
    p.add_argument("--run-id", required=True)
    p.add_argument("--run-attempt", default="1")

    p = sub.add_parser("run")
    p.add_argument("--command", dest="benchmark_command", choices=["execute-test", "compare"], default="execute-test")
    p.add_argument("--params", default="{}")
    p.add_argument("--suffix", default="")
    p.add_argument("--insecure", default="false")
    p.add_argument("--user-tag", default="")
    p.add_argument("--config", default="")
    p.add_argument("--benchmark-config", default="")
    p.add_argument("--bundle-manifest", default="")
    p.add_argument("--endpoint", default="")
    p.add_argument("--baseline", default="")
    p.add_argument("--contender", default="")
    p.add_argument("--print-only", action="store_true")

    p = sub.add_parser("credentials", help="pick username:password for CREDENTIALS_KEY from BENCHMARK_ENDPOINT_CREDENTIALS")
    p.add_argument("--key", required=True)

    p = sub.add_parser("edit-config")
    p.add_argument("path")

    p = sub.add_parser("compare-ids")
    p.add_argument("--baseline-cluster-config", required=True)
    p.add_argument("--distribution-version", required=True)
    p.add_argument("--workload", required=True)
    p.add_argument("--pull-request-number", required=True)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("comment")
    p.add_argument("--title", required=True)
    p.add_argument("--run-url", required=True)
    p.add_argument("--results", default="", help="final_result_<suffix>.md; empty renders the failure comment")
    p.add_argument("--output", required=True)

    p = sub.add_parser("webhook")
    p.add_argument("--icon", required=True)
    p.add_argument("--job-name", required=True)
    p.add_argument("--run-number", required=True)
    p.add_argument("--message", required=True)
    p.add_argument("--run-url", required=True)
    p.add_argument("--extra", default="")

    p = sub.add_parser("stacks")
    p.add_argument("--suffix", required=True)
    p.add_argument("--build-id", default="")
    p.add_argument("--architecture", default="")

    p = sub.add_parser("import-jenkins")
    p.add_argument("--workflow", required=True, choices=sorted(WORKFLOW_SUITES))

    args = parser.parse_args(argv)
    try:
        if args.command == "resolve":
            result = resolve(args.workflow, args.mode, json.loads(args.data or "{}"), args.schedule, args.run_id,
                             args.run_attempt, args.suite)
            write_outputs(result)
        elif args.command == "run":
            if args.benchmark_command == "compare":
                argv_test = compare_args(args.baseline, args.contender, args.suffix, args.benchmark_config)
            else:
                params = json.loads(args.params)
                argv_test = test_args(params, args.suffix, to_bool(args.insecure), args.user_tag, args.config,
                                      args.benchmark_config, args.bundle_manifest, args.endpoint)
            password = os.environ.get("BENCHMARK_CLUSTER_PASSWORD", "")
            username = os.environ.get("BENCHMARK_CLUSTER_USERNAME", "")
            if username:
                argv_test += ["--username", username]
            shown = list(argv_test)
            if password:
                argv_test += ["--password", password]
                shown += ["--password", "***"]
            print("./test.sh " + " ".join(shown), flush=True)
            if args.print_only:
                return 0
            return subprocess.call([os.path.join(ROOT, "test.sh")] + argv_test, cwd=os.getcwd())
        elif args.command == "credentials":
            mapping = json.loads(os.environ.get("BENCHMARK_ENDPOINT_CREDENTIALS") or "{}")
            if args.key not in mapping:
                raise ValueError(f"BENCHMARK_ENDPOINT_CREDENTIALS has no key '{args.key}'")
            username, _, password = str(mapping[args.key]).partition(":")
            print(f"::add-mask::{password}")
            write_github_env({"BENCHMARK_CLUSTER_USERNAME": username, "BENCHMARK_CLUSTER_PASSWORD": password})
        elif args.command == "edit-config":
            edit_config(args.path, os.environ.get("DATASTORE_USER", ""), os.environ.get("DATASTORE_PASSWORD", ""))
        elif args.command == "compare-ids":
            queries = compare_queries(args.baseline_cluster_config, args.distribution_version, args.workload, args.pull_request_number)
            if args.dry_run:
                print(json.dumps(queries, indent=2))
                write_outputs({"baseline": "", "contender": ""})
                return 0
            endpoint = os.environ["DATASTORE_ENDPOINT"]
            user, password = os.environ["DATASTORE_USER"], os.environ["DATASTORE_PASSWORD"]
            ids = {key: latest_execution_id(search(endpoint, user, password, body)) for key, body in queries.items()}
            for key, value in ids.items():
                print(f"Latest {key} test-execution-id: {value or '(no matching documents found)'}")
            write_outputs(ids)
        elif args.command == "comment":
            if args.results:
                with open(args.results) as f:
                    body = render_comment(args.title, args.run_url, f.read())
            else:
                body = failure_comment(args.run_url)
            with open(args.output, "w") as f:
                f.write(body)
            print(body)
        elif args.command == "webhook":
            sys.path.insert(0, os.path.dirname(__file__))
            from notify_failure import post_webhook, webhook_text
            text = webhook_text(args.icon, args.job_name, args.run_number, args.message, args.run_url, "", args.extra)
            print(json.dumps({"result_text": text}, indent=2))
            url = os.environ.get("WEBHOOK_URL", "")
            if url:
                print(f"webhook status: {post_webhook(url, text)}")
            else:
                print("WEBHOOK_URL is not set; not posting")
        elif args.command == "stacks":
            print("\n".join(stack_names(args.suffix, args.build_id, args.architecture)))
        elif args.command == "import-jenkins":
            header = (f"# Generated by `.github/scripts/benchmark_test.py import-jenkins --workflow {args.workflow}` from the\n"
                      f"# parameterizedCron blocks of {', '.join(SUITES[s]['jenkinsfile'] for s in WORKFLOW_SUITES[args.workflow])}.\n"
                      "# Jenkins `H` minutes become a fixed minute per suite; every cron here must also be listed under\n"
                      f"# on.schedule in .github/workflows/{args.workflow}.yml (tests/tests_github_scripts/test_benchmark_test.py checks).\n")
            print(header + "---\n" + yaml.dump(import_jenkins(args.workflow), Dumper=IndentedDumper, sort_keys=False, width=1000), end="")
    except ValueError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
