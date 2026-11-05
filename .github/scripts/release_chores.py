#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Release chores for the release-chore composite action and the release-notes workflows.

Port of opensearch-build-libraries (jenkins@12.0.0): vars/checkRequestAssignReleaseOwners.groovy,
vars/checkDocumentationIssues.groovy, vars/checkDocumentationPullRequests.groovy, vars/checkCodeCoverage.groovy,
vars/checkReleaseNotes.groovy, vars/checkReleaseIssues.groovy, vars/checkIntegTestResultsOverview.groovy,
vars/addRcDetailsComment.groovy and vars/buildRC.groovy, with the metrics queries of src/jenkins/ReleaseMetricsData,
ComponentRepoData, ReleaseCandidateStatus and ComponentIntegTestStatus. Also holds the pull request halves of
jenkins/release-workflows/release-notes-generate.jenkinsfile and release-notes-check-lf.jenkinsfile (compile).

With --dry-run every read (metrics, GitHub, git) runs for real and every write (issue comments, assignees,
workflow dispatches, pushes, pull requests) is printed instead.
"""

import argparse
import difflib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "actions", "release-chore", "templates")
UPSTREAM_OWNER = "opensearch-project"
DOCS_REPO = f"{UPSTREAM_OWNER}/documentation-website"
RELEASE_METRICS_INDEX = "opensearch_release_metrics"
BUILD_RESULTS_INDEX = "opensearch-distribution-build-results"
INTEG_RESULTS_INDEX = "opensearch-integration-test-results"
NOTES_SKIPPED_COMPONENTS = ("notifications-core", "functionalTestDashboards")
ARCH_DISTRIBUTIONS = {"x64": ["tar", "rpm", "deb", "zip"], "arm64": ["tar", "rpm", "deb"]}
BOT_NAME = "opensearch-ci-bot"
BOT_EMAIL = "opensearch-infra@amazon.com"

CHORE_ACTIONS = {
    "release-owners": ("check", "request", "assign"),
    "doc-issues": ("check", "notify"),
    "doc-prs": ("check",),
    "code-coverage": ("check", "notify"),
    "release-notes": ("check", "notify"),
    "release-issues": ("check", "create"),
    "integ-overview": ("check",),
    "rc-details": ("add",),
    "build-rc": ("both", "opensearch", "opensearch-dashboards"),
}


class ChoreError(Exception):
    pass


def echo(message: str) -> None:
    print(message, flush=True)


def base_version(version: str) -> str:
    return version.split("-")[0]


def qualifier_of(version: str) -> Optional[str]:
    parts = version.split("-", 1)
    return parts[1] if len(parts) > 1 and parts[1] else None


def month_year(today: Optional[date] = None) -> str:
    today = today or date.today()
    return f"{today.month:02d}-{today.year}"


def repo_name(repository: str) -> str:
    return repository.rstrip("/").split("/")[-1].replace(".git", "")


def render_template(name: str, bindings: Dict[str, Any]) -> str:
    """GStringTemplateEngine subset used by the library templates: $NAME placeholders and \\$ escapes."""
    with open(os.path.join(TEMPLATES, name)) as f:
        content = f.read()

    def substitute(match: "re.Match[str]") -> str:
        if match.group(0) == "\\$":
            return "$"
        key = match.group(1)
        if key not in bindings:
            raise ChoreError(f"Template {name} references unbound variable ${key}")
        return str(bindings[key])

    return re.sub(r"\\\$|\$([A-Za-z_][A-Za-z0-9_]*)", substitute, content)


def load_manifest(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        raise ChoreError(f"Invalid path. Input manifest file does not exist at {path}")
    with open(path) as f:
        result: Dict[str, Any] = yaml.safe_load(f)
        return result


class GitHub:
    """`gh` wrapper; write calls are printed instead of run in dry-run mode."""

    def __init__(self, dry_run: bool, runner: Optional[Callable[[List[str]], str]] = None) -> None:
        self.dry_run = dry_run
        self.runner = runner or self._run
        self.writes: List[str] = []

    @staticmethod
    def _run(args: List[str]) -> str:
        return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout.strip()

    def read(self, *args: str) -> str:
        return self.runner(list(args))

    def write(self, description: str, *args: str) -> str:
        self.writes.append(description)
        if self.dry_run:
            echo(f"[dry-run] would {description}")
            echo(f"[dry-run]   gh {' '.join(args)}")
            return ""
        echo(description)
        return self.runner(list(args))

    def comment(self, issue_url: str, body: str, repo: Optional[str] = None) -> None:
        args = ["issue", "comment", issue_url, "--body", body] + (["--repo", repo] if repo else [])
        if self.dry_run:
            self.writes.append(f"comment on {issue_url}{' (' + repo + ')' if repo else ''}")
            echo(f"[dry-run] would comment on issue {issue_url}{' in ' + repo if repo else ''}:")
            echo("\n".join("[dry-run]   | " + line for line in body.splitlines()))
            return
        echo(f"Commenting on issue {issue_url}")
        self.runner(args)


class MetricsClient:
    """SigV4-signed _search calls against the metrics cluster (OpenSearchMetricsQuery.fetchMetrics)."""

    def __init__(self, endpoint: str, region: str = "us-east-1", transport: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None) -> None:
        if not endpoint:
            raise ChoreError("METRICS_CLUSTER_ENDPOINT is not set; this chore reads the metrics cluster (AWS_ROLE_METRICS role)")
        self.endpoint = endpoint.rstrip("/")
        self.region = region
        self.transport = transport or self._signed_search

    def _signed_search(self, index: str, query: Dict[str, Any]) -> Dict[str, Any]:
        import boto3
        import requests
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        url = f"{self.endpoint}/{index}/_search"
        body = json.dumps(query)
        request = AWSRequest(method="GET", url=url, data=body, headers={"Content-Type": "application/json"})
        SigV4Auth(boto3.Session().get_credentials().get_frozen_credentials(), "es", self.region).add_auth(request)
        response = requests.get(url, data=body, headers=dict(request.headers), timeout=60)
        response.raise_for_status()
        result: Dict[str, Any] = response.json()
        return result

    def search(self, index: str, query: Dict[str, Any]) -> Dict[str, Any]:
        echo(f"Running query on {index}: {json.dumps(query)}")
        return self.transport(index, query)


def _sort_current_date() -> List[Dict[str, Any]]:
    return [{"current_date": {"order": "desc"}}]


class ReleaseMetrics:
    """Queries of ReleaseMetricsData, ComponentRepoData, ReleaseCandidateStatus and ComponentIntegTestStatus."""

    def __init__(self, client: MetricsClient, version: str, qualifier: Optional[str] = None) -> None:
        self.client = client
        self.version = version
        self.qualifier = qualifier

    def _first_source(self, index: str, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        hits = self.client.search(index, query).get("hits", {}).get("hits", [])
        if not hits:
            return None
        source: Dict[str, Any] = hits[0].get("_source", {})
        return source

    def _owner_issue_query(self, component: str) -> Dict[str, Any]:
        return {
            "size": 1,
            "_source": ["release_owners", "release_issue_exists"],
            "query": {"bool": {"filter": [{"match_phrase": {"version": self.version}}, {"match_phrase": {"component.keyword": component}}]}},
            "sort": _sort_current_date(),
        }

    def release_owners(self, component: str) -> Optional[List[str]]:
        """Owners of the latest metrics document; None when the component has no document (read errors propagate)."""
        hits = self.client.search(RELEASE_METRICS_INDEX, self._owner_issue_query(component))["hits"]["hits"]
        if not hits:
            return None
        owners: List[str] = []
        for hit in hits:
            value = hit.get("_source", {}).get("release_owners") or []
            owners.extend(value if isinstance(value, list) else [value])
        return owners

    def release_issue_exists(self, component: str) -> Optional[bool]:
        try:
            source = self._first_source(RELEASE_METRICS_INDEX, self._owner_issue_query(component))
            return None if source is None else bool(source.get("release_issue_exists"))
        except Exception as ex:
            echo(f"Error fetching release issue status: {ex}")
            return None

    def release_issue(self, repository: str, key: str = "repository.keyword") -> Optional[str]:
        query = {
            "size": 1,
            "_source": "release_issue",
            "query": {"bool": {"filter": [{"match_phrase": {"version": self.version}}, {"match_phrase": {key: repository}}]}},
            "sort": _sort_current_date(),
        }
        try:
            source = self._first_source(RELEASE_METRICS_INDEX, query)
            return None if source is None or not source.get("release_issue") else str(source["release_issue"])
        except Exception as ex:
            echo(f"Error fetching release issue: {ex}")
            return None

    def maintainers(self, repository: str, index: str, inactive: bool = False) -> Optional[List[str]]:
        query = {
            "size": 100,
            "_source": "github_login",
            "collapse": {"field": "github_login.keyword"},
            "query": {"bool": {"filter": [{"match_phrase": {"repository.keyword": repository}}, {"match_phrase": {"inactive": str(inactive).lower()}}]}},
            "sort": _sort_current_date(),
        }
        try:
            hits = self.client.search(index, query)["hits"]["hits"]
            return [hit["_source"]["github_login"] for hit in hits]
        except Exception as ex:
            echo(f"Error fetching the maintainers for {repository}: {ex}")
            return None

    def code_coverage(self, repository: str, index: str) -> Optional[Dict[str, Any]]:
        query = {
            "size": 1,
            "_source": ["coverage", "branch", "state", "url"],
            "query": {"bool": {"filter": [{"match_phrase": {"repository.keyword": repository}}, {"match_phrase": {"version": self.version}}]}},
            "sort": _sort_current_date(),
        }
        try:
            source = self._first_source(index, query)
            if source is None:
                raise ChoreError("no hits")
            return {key: source.get(key) for key in ("coverage", "state", "branch", "url")}
        except Exception as ex:
            echo(f"Error fetching code coverage metrics for {repository}: {ex}")
            return None

    def _rc_filters(self, component: str) -> List[Dict[str, Any]]:
        return [{"match_phrase": {"component": component}}, {"match_phrase": {"rc": "true"}}, {"match_phrase": {"version": self.version}}]

    def _qualifier_filter(self) -> List[Dict[str, Any]]:
        return [{"match_phrase": {"qualifier": self.qualifier}}] if self.qualifier and self.qualifier not in ("None", "Null") else []

    def latest_rc_number(self, component: str) -> Optional[int]:
        query = {
            "_source": "rc_number",
            "sort": [{"distribution_build_number": {"order": "desc"}, "rc_number": {"order": "desc"}}],
            "size": 1,
            "query": {"bool": {"filter": self._rc_filters(component) + [{"match_phrase": {"overall_build_result": "SUCCESS"}}] + self._qualifier_filter()}},
        }
        try:
            response = self.client.search(BUILD_RESULTS_INDEX, query)
            if response["hits"]["total"]["value"] == 0:
                echo(f"No successful RC builds found for component: {component}, returning latest RC number as 0")
                return 0
            return int(response["hits"]["hits"][0]["_source"]["rc_number"])
        except Exception as ex:
            echo(f"Error retrieving RC number for {component}: {ex}")
            return None

    def rc_distribution_number(self, rc_number: int, component: str) -> Any:
        query = {
            "_source": "distribution_build_number",
            "sort": [{"distribution_build_number": {"order": "desc"}}],
            "size": 1,
            "query": {"bool": {"filter": self._rc_filters(component) + [{"match_phrase": {"rc_number": str(rc_number)}}] + self._qualifier_filter()}},
        }
        return self.client.search(BUILD_RESULTS_INDEX, query)["hits"]["hits"][0]["_source"]["distribution_build_number"]

    def failed_integ_components(self, rc_number: int, distribution: str, architecture: str, components: List[str]) -> List[str]:
        must: List[Dict[str, Any]] = [
            {"match_phrase": {"rc_number": str(rc_number)}},
            {"match_phrase": {"version": self.version}},
            {"match_phrase": {"distribution": distribution}},
            {"match_phrase": {"architecture": architecture}},
            {"terms": {"component": components}},
        ]
        must += self._qualifier_filter()
        if "OpenSearch-Dashboards" in components:
            must = [clause for clause in must if "terms" not in clause]
            must.append({"bool": {"should": [{"regexp": {"component": "OpenSearch-Dashboards-ci-group-.*"}}, {"terms": {"component": components}}]}})
        query = {
            "size": 100,
            "sort": [{"build_start_time": {"order": "desc"}}],
            "_source": ["component", "component_build_result"],
            "query": {"bool": {"must": must}},
            "collapse": {"field": "component"},
        }
        hits = self.client.search(INTEG_RESULTS_INDEX, query)["hits"]["hits"]
        return [hit["_source"]["component"] for hit in hits if hit["_source"].get("component_build_result") == "failed"]


class Chores:
    def __init__(self, args: argparse.Namespace, github: GitHub, metrics_factory: Callable[[str, Optional[str]], ReleaseMetrics]) -> None:
        self.args = args
        self.github = github
        self.metrics_factory = metrics_factory
        self.dry_run = github.dry_run

    def _manifests(self) -> List[Dict[str, Any]]:
        if not self.args.manifest:
            raise ChoreError("inputManifest parameter is required.")
        return [load_manifest(path) for path in self.args.manifest]

    def _require_version(self) -> str:
        if not self.args.version:
            raise ChoreError("Version parameter is required.")
        return str(self.args.version)

    # checkRequestAssignReleaseOwners
    def release_owners(self) -> None:
        manifests = self._manifests()
        metrics = self.metrics_factory(str(manifests[0]["build"]["version"]), None)
        missing: List[str] = []
        unknown: List[str] = []
        for manifest in manifests:
            for component in manifest.get("components", []):
                owners = metrics.release_owners(component["name"])
                if owners is None:
                    unknown.append(component["name"])
                    continue
                if not owners:
                    missing.append(component["name"])
                    if self.args.action != "check":
                        self._handle_missing_owner(component, metrics)
        if unknown:
            echo(f"Components without a release metrics document (owner unknown, not requested/assigned): {unknown}")
        if missing:
            echo(f"Components missing release owner: {missing}")
        elif not unknown:
            echo("All components have release owner assigned.")

    def _handle_missing_owner(self, component: Dict[str, Any], metrics: ReleaseMetrics) -> None:
        name = repo_name(component["repository"])
        issue = metrics.release_issue(name)
        maintainers = metrics.maintainers(name, f"maintainer-inactivity-{month_year()}")
        if not maintainers:
            echo(f"No maintainers found for component: {component['name']}. Skipping action.")
            return
        tagged = [f"@{login}" for login in maintainers]
        if self.args.action == "request":
            self.github.comment(issue, render_template("request-release-owner-template.md", {"LIST_OF_MAINTAINERS": ", ".join(tagged)}))
        else:
            owner = random.choice(tagged)
            self.github.comment(issue, render_template("release-owner-assignment-template.md", {"RELEASE_OWNER": owner}))
            self.github.write(f"assign {owner.lstrip('@')} to {issue}", "issue", "edit", issue, "--add-assignee", owner.lstrip("@"))

    # checkDocumentationIssues
    def doc_issues(self) -> None:
        version = base_version(self._require_version())
        numbers = self.github.read("issue", "list", "--repo", DOCS_REPO, "--state", "open", "--label", f"v{version}", "-S", "-linked:pr", "--json", "number", "--jq", ".[].number")
        issues = [line.strip() for line in numbers.splitlines() if line.strip()]
        if not issues:
            echo("No open documentation issues found without a linked PR!")
            return
        echo(f"Open documentation issues found. Issue numbers: {issues}")
        if self.args.action == "notify":
            for number in issues:
                owner = self.github.read("issue", "view", number, "--repo", DOCS_REPO, "--json", "assignees", "--jq", ".assignees[0].login")
                if not owner.strip() or owner.strip() == "null":
                    owner = self.github.read("issue", "view", number, "--repo", DOCS_REPO, "--json", "author", "--jq", ".author.login")
                self.github.comment(number, render_template("documentation-issues-template.md", {"OWNER": owner.strip()}), repo=DOCS_REPO)

    # checkDocumentationPullRequests
    def doc_prs(self) -> None:
        version = base_version(self._require_version())
        urls = self.github.read("pr", "list", "--repo", DOCS_REPO, "--state", "open", "--label", f"v{version}", "-S", '-label:"6 - Done but waiting to merge"', "--json", "url", "--jq", ".[].url")
        echo(f"Documentation pull requests pending to be merged: \n{urls}" if urls.strip() else "No open pull requests found!")

    # checkCodeCoverage
    def code_coverage(self) -> None:
        manifests = self._manifests()
        version = base_version(str(manifests[0]["build"]["version"]))
        metrics = self.metrics_factory(version, None)
        index = f"opensearch-codecov-metrics-{month_year()}"
        missing: Dict[str, Any] = {}
        for manifest in manifests:
            for component in manifest.get("components", []):
                name = repo_name(component["repository"])
                coverage = metrics.code_coverage(name, index)
                issue = metrics.release_issue(name)
                if coverage and coverage.get("state") == "no-coverage":
                    missing[component["name"]] = coverage.get("url")
                    if self.args.action == "notify":
                        body = render_template("missing-code-coverage.md", {"BRANCH": coverage.get("branch"), "CODECOV_URL": coverage.get("url"), "COMPONENT_NAME": component["name"]})
                        self.github.comment(issue, body)
        echo(f"Components missing code coverage are: {missing}" if missing else "All components are reporting code coverage.")

    # checkReleaseNotes
    def release_notes(self) -> None:
        version = base_version(self._require_version())
        if not self.args.table or not os.path.isfile(self.args.table):
            raise ChoreError(f"Invalid path.Data Table file does not exist at {self.args.table}")
        with open(self.args.table) as f:
            rows = parse_release_notes_table(f.read())
        missing = [row for row in rows if row["Status"] == "False"]
        echo(f"Components missing release notes: {[row['Component'] for row in missing]}")
        if self.args.action == "notify" and missing:
            echo("Notifying all the components with missing release notes.")
            metrics = self.metrics_factory(version, None)
            for row in missing:
                if row["Component"] == "functionalTestDashboards":
                    continue
                issue = metrics.release_issue(row["Component"], "component.keyword")
                self.github.comment(issue, render_template("missing-release-notes.md", {"BRANCH": row["Branch"]}))

    # checkReleaseIssues
    def release_issues(self) -> None:
        manifests = self._manifests()
        metrics = self.metrics_factory(str(manifests[0]["build"]["version"]), None)
        missing = []
        for component in [c for m in manifests for c in m.get("components", [])]:
            # As checkReleaseIssues.groovy: `!getReleaseIssueStatus(name)` (null on a failed/empty metrics read) counts as missing.
            if not metrics.release_issue_exists(component["name"]):
                missing.append(component["name"])
        echo(f"Components missing release issues: {missing}")
        if self.args.action == "create" and missing:
            echo("Triggering GitHub workflows for creating issues:")
            for workflow in ("os-release-issues.yml", "osd-release-issues.yml"):
                self.github.write(f"dispatch {workflow} in {self.args.repository}", "workflow", "run", workflow, "-R", self.args.repository)

    # checkIntegTestResultsOverview
    def integ_overview(self) -> None:
        manifests = self._manifests()
        os_components: List[str] = []
        osd_components: List[str] = []
        for manifest in manifests:
            names = [c["name"] for c in manifest.get("components", [])]
            (os_components if manifest["build"]["name"] == "OpenSearch" else osd_components).extend(names)
        build = manifests[0]["build"]
        metrics = self.metrics_factory(str(build["version"]), build.get("qualifier") or None)
        os_rc = metrics.latest_rc_number("OpenSearch")
        osd_rc = metrics.latest_rc_number("OpenSearch-Dashboards")
        if not os_rc or not osd_rc:
            raise ChoreError("Unable to fetch latest RC number from metrics. Received null or 0 value.")
        lines = []
        for arch, distributions in ARCH_DISTRIBUTIONS.items():
            for dist in distributions:
                failed = metrics.failed_integ_components(os_rc, dist, arch, os_components) + metrics.failed_integ_components(osd_rc, dist, arch, osd_components)
                lines.append(f"{dist}_{arch}: {failed}")
        echo("Components failing integration tests:\n" + "\n".join(lines))

    def _rc_metrics(self) -> Tuple[ReleaseMetrics, str]:
        version = self._require_version()
        return self.metrics_factory(base_version(version), qualifier_of(version) or "None"), base_version(version)

    # addRcDetailsComment
    def rc_details(self) -> None:
        metrics, version = self._rc_metrics()
        issue = metrics.release_issue("opensearch-build")
        os_rc = self.args.opensearch_rc_number or metrics.latest_rc_number("OpenSearch")
        osd_rc = self.args.opensearch_dashboards_rc_number or metrics.latest_rc_number("OpenSearch-Dashboards")
        echo(f"Retrieved RC numbers: OpenSearch - {os_rc}, OpenSearch-Dashboards - {osd_rc}")
        if not os_rc or not osd_rc:
            raise ChoreError("Unable to fetch latest RC number from metrics. Received null or 0 value.")
        os_build = str(metrics.rc_distribution_number(int(os_rc), "OpenSearch"))
        osd_build = str(metrics.rc_distribution_number(int(osd_rc), "OpenSearch-Dashboards"))
        scan_url = f"{self.args.server_url}/{self.args.repository}/actions/workflows/docker-scan.yml"
        bindings = {
            "VERSION": version,
            "OPENSEARCH_RC_NUMBER": os_rc,
            "OPENSEARCH_DASHBOARDS_RC_NUMBER": osd_rc,
            "OPENSEARCH_RC_BUILD_NUMBER": os_build,
            "OPENSEARCH_DASHBOARDS_RC_BUILD_NUMBER": osd_build,
            "OPENSEARCH_DOCKER_SCAN_RESULTS": f"Unable to get docker scan results for OpenSearch, RC build number: {os_build}",
            "OPENSEARCH_DASHBOARDS_DOCKER_SCAN_RESULTS": f"Unable to get docker scan results for OpenSearch-Dashboards, RC build number: {osd_build}",
            "OPENSEARCH_DOCKER_SCAN_URL": scan_url,
            "OPENSEARCH_DASHBOARDS_DOCKER_SCAN_URL": scan_url,
        }
        echo("Adding RC details to the release issue as a comment.")
        self.github.comment(issue, render_template("rc-details-template.md", bindings))

    # buildRC
    def build_rc(self) -> None:
        metrics, _ = self._rc_metrics()
        version = self._require_version()
        product = self.args.action or "both"
        os_rc = metrics.latest_rc_number("OpenSearch")
        osd_rc = metrics.latest_rc_number("OpenSearch-Dashboards")
        echo(f"Retrieved Current RC numbers: OpenSearch - {os_rc}, OpenSearch-Dashboards - {osd_rc}")
        targets = {"opensearch": [("opensearch", os_rc)], "opensearch-dashboards": [("opensearch-dashboards", osd_rc)], "both": [("opensearch", os_rc), ("opensearch-dashboards", osd_rc)]}[product]
        if any(rc is None for _, rc in targets):
            raise ChoreError("Unable to fetch latest RC number from metrics. Received null value.")
        for target, rc in targets:
            echo(f"Triggering {target} build with RC number: {int(rc) + 1}")
            self._trigger_build(version, target, str(int(rc) + 1))

    def _trigger_build(self, version: str, product: str, rc_number: str) -> None:
        workflow = f"distribution-build-{product}.yml"
        if not os.path.isfile(os.path.join(".github", "workflows", workflow)):
            message = f".github/workflows/{workflow} does not exist yet (plan steps s4.1 / s4.2)"
            if not self.dry_run:
                raise ChoreError(message)
            echo(f"::warning::{message}")
        fields = {
            "INPUT_MANIFEST": f"{version}/{product}-{version}.yml",
            "TEST_MANIFEST": f"{version}/{product}-{version}-test.yml",
            "BUILD_PLATFORM": "linux windows",
            "BUILD_DISTRIBUTION": "tar rpm deb zip",
            "TEST_PLATFORM": "linux windows",
            "TEST_DISTRIBUTION": "tar rpm deb zip",
            "RC_NUMBER": rc_number,
            "BUILD_DOCKER": "build_docker_with_build_number_tag",
            "CONTINUE_ON_ERROR": "false",
            "UPDATE_GITHUB_ISSUE": "true",
        }
        if product == "opensearch":
            # distribution-build-opensearch.yml packs the parameters past the ten-input limit into OPTIONS
            fields = {
                "INPUT_MANIFEST": fields["INPUT_MANIFEST"],
                "TEST_MANIFEST": fields["TEST_MANIFEST"],
                "BUILD_PLATFORM": fields["BUILD_PLATFORM"],
                "BUILD_DISTRIBUTION": fields["BUILD_DISTRIBUTION"],
                "RC_NUMBER": rc_number,
                "CONTINUE_ON_ERROR": "false",
                "OPTIONS": "test-platform=linux,windows test-distribution=tar,rpm,deb,zip "
                           "build-docker=build_docker_with_build_number_tag update-github-issue",
            }
        args = ["workflow", "run", workflow, "-R", self.args.repository]
        for key, value in fields.items():
            args += ["-f", f"{key}={value}"]
        self.github.write(f"dispatch {workflow} in {self.args.repository} with RC_NUMBER={rc_number}", *args)

    def run(self, chore: str) -> None:
        if self.args.action not in CHORE_ACTIONS[chore]:
            raise ChoreError(f"Invalid action '{self.args.action}'. Valid values: {', '.join(CHORE_ACTIONS[chore])}")
        getattr(self, chore.replace("-", "_"))()


def parse_release_notes_table(markdown: str) -> List[Dict[str, str]]:
    """ParseReleaseNotesMarkdownTable: rows of the release_notes.sh check table, header and separator dropped."""
    rows = [line for line in markdown.splitlines() if line.startswith("|")][2:]
    parsed = []
    for row in rows:
        cells = row.split("|")
        parsed.append({"Component": cells[1].strip(), "Branch": cells[2].strip(), "Commit ID": cells[3].strip(), "Commit Date": cells[4].strip(), "Status": cells[5].strip()})
    return parsed


class GitHubReleaseMetrics(ReleaseMetrics):
    """Dry-run stand-in when the metrics cluster is not reachable: release issues come from a GitHub title search."""

    def __init__(self, github: GitHub, version: str, manifests: List[str]) -> None:
        self.github = github
        self.version = version
        self.qualifier = None
        self.repos: Dict[str, str] = {}
        for path in manifests:
            for component in load_manifest(path).get("components", []):
                self.repos[component["name"]] = repo_name(component["repository"])

    def _issue(self, component_or_repo: str) -> Dict[str, Any]:
        repository = self.repos.get(component_or_repo, component_or_repo)
        found = self.github.read("search", "issues", f"[RELEASE] Release version {self.version} in:title", "--repo", f"{UPSTREAM_OWNER}/{repository}", "--json", "url,assignees", "--limit", "1")
        results = json.loads(found or "[]")
        result: Dict[str, Any] = results[0] if results else {}
        return result

    def release_issue(self, repository: str, key: str = "repository.keyword") -> Optional[str]:
        url = self._issue(repository).get("url")
        return str(url) if url else None

    def release_issue_exists(self, component: str) -> Optional[bool]:
        return bool(self._issue(component))

    def release_owners(self, component: str) -> Optional[List[str]]:
        return [assignee["login"] for assignee in self._issue(component).get("assignees", [])]

    def maintainers(self, repository: str, index: str, inactive: bool = False) -> Optional[List[str]]:
        return None


def clone(repository: str, ref: str, destination: str) -> None:
    branch = ref[len("tags/"):] if ref.startswith("tags/") else ref.replace("refs/heads/", "")
    subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", branch, repository, destination], check=True)


def compare_notes(committed: Optional[str], generated: str, label: str) -> Dict[str, Any]:
    """Diff the generated notes against the committed file and compare the pull request references they cite."""
    pr_refs = re.compile(r"/pull/(\d+)")
    generated_refs = set(pr_refs.findall(generated))
    if committed is None:
        echo(f"No committed {label} to compare with.")
        return {"committed": False, "generated_prs": sorted(generated_refs, key=int)}
    committed_refs = set(pr_refs.findall(committed))
    diff = list(difflib.unified_diff(committed.splitlines(), generated.splitlines(), f"committed/{label}", f"generated/{label}", lineterm=""))
    summary = {
        "committed": True,
        "identical": not diff,
        "committed_prs": len(committed_refs),
        "generated_prs": len(generated_refs),
        "common_prs": sorted(committed_refs & generated_refs, key=int),
        "only_committed": sorted(committed_refs - generated_refs, key=int),
        "only_generated": sorted(generated_refs - committed_refs, key=int),
    }
    echo(f"--- comparison with committed {label} ---")
    echo("\n".join(diff) if diff else "(identical)")
    echo(json.dumps(summary))
    echo(f"--- end comparison with committed {label} ---")
    return summary


def notes_filename(component: Dict[str, Any], version: str, product: str) -> str:
    if component["name"].lower() in ("opensearch", "opensearch-dashboards"):
        return f"{component['name'].lower()}.release-notes-{version}.md"
    return f"opensearch-{repo_name(component['repository'])}.release-notes-{version}.0.md"


def notes_matrix(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest[0])
    names = [c["name"] for c in manifest.get("components", [])]
    selected = args.components.split() if args.components else names
    unknown = [name for name in selected if name not in names]
    if unknown:
        raise ChoreError(f"Components not in {args.manifest[0]}: {unknown}")
    selected = [name for name in selected if name not in NOTES_SKIPPED_COMPONENTS]
    repos = {c["name"]: repo_name(c["repository"]) for c in manifest.get("components", [])}
    matrix = {"include": [{"component": name, "repository": repos[name], "index": index} for index, name in enumerate(selected)]}
    echo(f"Component list: {selected}")
    write_output("matrix", json.dumps(matrix))
    write_output("count", str(len(selected)))


def write_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"{name}={value}\n")
    echo(f"{name}={value}")


def git(cwd: str, *args: str, dry_run: bool = False, description: str = "") -> None:
    if dry_run:
        shown = " ".join(re.sub(r"x-access-token:[^@]*@", "x-access-token:***@", arg) for arg in args)
        echo(f"[dry-run] would {description}: git {shown}")
        return
    subprocess.run(["git", *args], cwd=cwd, check=True)


def push_url(repository: str) -> str:
    return f"https://x-access-token:{os.environ.get('GH_TOKEN', '')}@github.com/{repository}.git"


def pr_base(ref: str) -> str:
    """PR base, as Jenkins release-notes-generate: `gh pr create -B ${ref}` with the manifest/REF value verbatim.

    Jenkins cannot handle a tag ref (`git fetch upstream tags/...` / `checkout upstream/tags/...` fail before the PR),
    so a tag fails here too, before any write.
    """
    if ref.startswith("tags/"):
        raise ChoreError(f"ref '{ref}' is a tag; Jenkins release-notes-generate opens the notes PR against the ref (-B {ref}), "
                         "which must be a branch. Re-run with REF set to the release branch.")
    return ref


def notes_pr(args: argparse.Namespace, github: GitHub) -> None:
    """Pull request half of release-notes-generate: one '[AUTO] Add release notes' PR per component repository."""
    manifest = load_manifest(args.manifest[0])
    component = next((c for c in manifest.get("components", []) if c["name"] == args.component), None)
    if component is None:
        raise ChoreError(f"Component {args.component} not in {args.manifest[0]}")
    version = str(manifest["build"]["version"])
    product = "opensearch" if manifest["build"]["name"] == "OpenSearch" else "opensearch-dashboards"
    filename = notes_filename(component, version, product)
    generated_path = os.path.join(args.notes_dir, filename)
    if not os.path.isfile(generated_path):
        raise ChoreError(f"File {generated_path} does not exist as there are no commits since {args.git_log_date}.")
    with open(generated_path) as f:
        generated = f.read()
    borderline_path = generated_path.replace(".md", "-borderline.md")
    borderline = open(borderline_path).read() if os.path.isfile(borderline_path) else ""
    ref = args.ref or component["ref"]
    name = repo_name(component["repository"])
    upstream = f"{UPSTREAM_OWNER}/{name}"
    branch = f"release-chores/release-notes-{version}"
    body = f"Add release notes for {version}\n"
    if borderline:
        body += f"\n## Borderline Calls\n{borderline}"
    echo("--- PR body content ---")
    echo(body)
    echo("--- end PR body content ---")

    work = tempfile.mkdtemp()
    try:
        checkout = os.path.join(work, name)
        clone(component["repository"], ref, checkout)
        target = os.path.join(checkout, "release-notes", filename)
        committed = open(target).read() if os.path.isfile(target) else None
        if args.dry_run:
            compare_notes(committed, generated, f"release-notes/{filename}")
        try:
            base = pr_base(ref)
        except ChoreError as error:
            if not args.dry_run:
                raise
            echo(f"[dry-run] would fail like Jenkins: {error}")
            return
        if committed == generated:
            echo(f"No changes to commit for component {name}. Skipping PR creation.")
            return
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(generated_path, target)
        git(checkout, "checkout", "-b", branch, dry_run=args.dry_run, description="create branch")
        git(checkout, "add", ".", dry_run=args.dry_run, description="stage release notes")
        git(checkout, "-c", f"user.email={args.git_email}", "-c", f"user.name={args.git_name}", "commit", "-sm", f"Add release notes for {version}", dry_run=args.dry_run, description="commit")
        git(checkout, "push", push_url(upstream), f"HEAD:refs/heads/{branch}", "--force", dry_run=args.dry_run, description=f"push {branch} to {upstream}")
        existing = github.read("api", f"repos/{upstream}/pulls?head={UPSTREAM_OWNER}:{branch}&state=open", "--jq", ".[0].number")
        if existing and existing != "null":
            github.write(f"update PR #{existing} in {upstream} with new release notes", "pr", "edit", existing, "--repo", upstream, "--body", body)
        else:
            title = f"[AUTO] Add release notes for {version}"
            github.write(f"open PR '{title}' in {upstream} ({branch} -> {base})", "pr", "create", "--repo", upstream, "--title", title, "--body", body, "-H", branch, "-B", base)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def notes_compiled_pr(args: argparse.Namespace, github: GitHub) -> None:
    """Pull request half of release-notes-tracker compile: consolidated release notes PR on this repository."""
    version = args.version
    path = os.path.join("release-notes", f"opensearch-release-notes-{version}.md")
    if not os.path.isfile(path):
        raise ChoreError(f"{path} was not compiled")
    with open(path) as f:
        compiled = f.read()
    committed = subprocess.run(["git", "show", f"HEAD:{path}"], capture_output=True, text=True)
    if args.dry_run:
        compare_notes(committed.stdout if committed.returncode == 0 else None, compiled, path)
    if not subprocess.run(["git", "status", "--porcelain", "--", path], capture_output=True, text=True, check=True).stdout.strip():
        echo("Nothing to commit!")
        return
    branch = "release-notes/compiled"
    title = f"Add consolidated release notes for {version}"
    git(".", "checkout", "-b", branch, dry_run=args.dry_run, description="create branch")
    git(".", "add", path, dry_run=args.dry_run, description="stage consolidated release notes")
    git(".", "-c", f"user.email={args.git_email}", "-c", f"user.name={args.git_name}", "commit", "-sm", title, dry_run=args.dry_run, description="commit")
    git(".", "push", push_url(args.repository), f"HEAD:refs/heads/{branch}", "--force", dry_run=args.dry_run, description=f"push {branch}")
    owner = args.repository.split("/")[0]
    existing = github.read("api", f"repos/{args.repository}/pulls?head={owner}:{branch}&state=open", "--jq", ".[0].number")
    if existing and existing != "null":
        github.write(f"update PR #{existing} in {args.repository}", "pr", "edit", existing, "--repo", args.repository, "--body", title)
    else:
        github.write(f"open PR '{title}' in {args.repository} ({branch} -> main)", "pr", "create", "--repo", args.repository, "--title", title, "--body", title, "-H", branch, "-B", "main")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=sorted(CHORE_ACTIONS) + ["notes-matrix", "notes-pr", "notes-compiled-pr"])
    parser.add_argument("--action", default="")
    parser.add_argument("--version", default="")
    parser.add_argument("--manifest", action="append", default=[])
    parser.add_argument("--table", default="")
    parser.add_argument("--component", default="")
    parser.add_argument("--components", default="")
    parser.add_argument("--ref", default="")
    parser.add_argument("--git-log-date", default="")
    parser.add_argument("--notes-dir", default="release-notes")
    parser.add_argument("--opensearch-rc-number", type=int, default=None)
    parser.add_argument("--opensearch-dashboards-rc-number", type=int, default=None)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", "opensearch-project/opensearch-build"))
    parser.add_argument("--server-url", default=os.environ.get("GITHUB_SERVER_URL", "https://github.com"))
    parser.add_argument("--metrics-endpoint", default=os.environ.get("METRICS_CLUSTER_ENDPOINT", ""))
    parser.add_argument("--git-name", default=BOT_NAME)
    parser.add_argument("--git-email", default=BOT_EMAIL)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    github = GitHub(args.dry_run)

    def metrics_factory(version: str, qualifier: Optional[str]) -> ReleaseMetrics:
        if not args.metrics_endpoint and args.dry_run and args.command in ("release-notes", "release-issues", "release-owners"):
            echo("::notice::METRICS_CLUSTER_ENDPOINT is not set; dry run looks release issues up by title on GitHub instead")
            return GitHubReleaseMetrics(github, version, args.manifest or [f"manifests/{version}/opensearch-{version}.yml", f"manifests/{version}/opensearch-dashboards-{version}.yml"])
        return ReleaseMetrics(MetricsClient(args.metrics_endpoint), version, qualifier)

    try:
        if args.command == "notes-matrix":
            notes_matrix(args)
        elif args.command == "notes-pr":
            notes_pr(args, github)
        elif args.command == "notes-compiled-pr":
            notes_compiled_pr(args, github)
        else:
            Chores(args, github, metrics_factory).run(args.command)
    except (ChoreError, subprocess.CalledProcessError) as ex:
        detail = getattr(ex, "stderr", "") or ""
        echo(f"::error::{ex} {detail}".strip())
        return 1
    if args.dry_run and github.writes:
        echo(f"[dry-run] {len(github.writes)} write(s) skipped:")
        for write in github.writes:
            echo(f"[dry-run]   - {write}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
