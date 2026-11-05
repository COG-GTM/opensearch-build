# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import importlib.util
import json
import os
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts", "release_chores.py")
SPEC = importlib.util.spec_from_file_location("release_chores", SCRIPT)
release_chores: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_chores)

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
OS_MANIFEST = os.path.join(ROOT, "manifests", "3.9.0", "opensearch-3.9.0.yml")
OSD_MANIFEST = os.path.join(ROOT, "manifests", "3.9.0", "opensearch-dashboards-3.9.0.yml")

TABLE = """# Core Components CommitID(after 2026-05-27) & Release Notes info
|     Repo      | Branch |CommitID|Commit Date|Release Notes Exists|
|---------------|--------|--------|-----------|--------------------|
|alerting       |[3.9]   |abc     |2026-07-01 |True                |
|job-scheduler  |[3.9]   |def     |2026-07-02 |False               |
|functionalTestDashboards|[3.9]|   |           |False               |
"""


class FakeGh:
    def __init__(self, responses: Optional[Dict[str, str]] = None) -> None:
        self.calls: List[List[str]] = []
        self.responses = responses or {}

    def __call__(self, args: List[str]) -> str:
        self.calls.append(args)
        for prefix, response in self.responses.items():
            if " ".join(args).startswith(prefix):
                return response
        return ""


class FakeMetrics:
    """Transport stub keyed on index; records queries."""

    def __init__(self, responses: Dict[str, Any]) -> None:
        self.responses = responses
        self.queries: List[Any] = []

    def __call__(self, index: str, query: Dict[str, Any]) -> Dict[str, Any]:
        self.queries.append((index, query))
        response = self.responses.get(index)
        result: Dict[str, Any] = response(query) if callable(response) else response
        return result


def hits(*sources: Dict[str, Any]) -> Dict[str, Any]:
    return {"hits": {"total": {"value": len(sources)}, "hits": [{"_source": s} for s in sources]}}


def chores(argv: List[str], gh: FakeGh, transport: Optional[FakeMetrics] = None) -> Any:
    args = release_chores.parse_args(argv + ["--repository", "COG-GTM/opensearch-build", "--metrics-endpoint", "https://metrics.example"])
    github = release_chores.GitHub("--dry-run" in argv, gh)

    def factory(version: str, qualifier: Optional[str]) -> Any:
        return release_chores.ReleaseMetrics(release_chores.MetricsClient(args.metrics_endpoint, transport=transport), version, qualifier)

    return release_chores.Chores(args, github, factory), github


def test_parse_release_notes_table() -> None:
    rows = release_chores.parse_release_notes_table(TABLE)
    assert [r["Component"] for r in rows] == ["alerting", "job-scheduler", "functionalTestDashboards"]
    assert rows[1] == {"Component": "job-scheduler", "Branch": "[3.9]", "Commit ID": "def", "Commit Date": "2026-07-02", "Status": "False"}


def test_render_template_handles_escapes_and_bindings() -> None:
    body = release_chores.render_template("missing-release-notes.md", {"BRANCH": "[3.9]"})
    assert "missing release notes at [3.9] ref" in body
    rc = release_chores.render_template("rc-details-template.md", {k: "X" for k in (
        "VERSION", "OPENSEARCH_RC_NUMBER", "OPENSEARCH_DASHBOARDS_RC_NUMBER", "OPENSEARCH_RC_BUILD_NUMBER", "OPENSEARCH_DASHBOARDS_RC_BUILD_NUMBER",
        "OPENSEARCH_DOCKER_SCAN_RESULTS", "OPENSEARCH_DASHBOARDS_DOCKER_SCAN_RESULTS", "OPENSEARCH_DOCKER_SCAN_URL", "OPENSEARCH_DASHBOARDS_DOCKER_SCAN_URL")})
    assert "('$state':(store:appState)" in rc
    assert "\\$state" not in rc
    with pytest.raises(release_chores.ChoreError):
        release_chores.render_template("missing-release-notes.md", {})


def test_release_notes_notify_comments_on_release_issue(tmp_path: Any) -> None:
    table = tmp_path / "table.md"
    table.write_text(TABLE)
    transport = FakeMetrics({release_chores.RELEASE_METRICS_INDEX: hits({"release_issue": "https://github.com/opensearch-project/job-scheduler/issues/1"})})
    gh = FakeGh()
    chore, github = chores(["release-notes", "--action", "notify", "--version", "3.9.0", "--table", str(table)], gh, transport)
    chore.run("release-notes")
    assert len(transport.queries) == 1
    _, query = transport.queries[0]
    assert query["query"]["bool"]["filter"][1] == {"match_phrase": {"component.keyword": "job-scheduler"}}
    assert gh.calls[0][:3] == ["issue", "comment", "https://github.com/opensearch-project/job-scheduler/issues/1"]
    assert "missing release notes at [3.9] ref" in gh.calls[0][4]


def test_release_notes_check_only_reports(tmp_path: Any, capsys: Any) -> None:
    table = tmp_path / "table.md"
    table.write_text(TABLE)
    gh = FakeGh()
    chore, _ = chores(["release-notes", "--action", "check", "--version", "3.9.0", "--table", str(table)], gh)
    chore.run("release-notes")
    assert gh.calls == []
    assert "['job-scheduler', 'functionalTestDashboards']" in capsys.readouterr().out


def test_doc_issues_notify_dry_run_prints_comments(capsys: Any) -> None:
    gh = FakeGh({"issue list": "12\n34", "issue view 12 --repo opensearch-project/documentation-website --json assignees": "alice",
                 "issue view 34 --repo opensearch-project/documentation-website --json assignees": "null",
                 "issue view 34 --repo opensearch-project/documentation-website --json author": "bob"})
    chore, github = chores(["doc-issues", "--action", "notify", "--version", "3.9.0", "--dry-run"], gh)
    chore.run("doc-issues")
    assert gh.calls[0][:8] == ["issue", "list", "--repo", "opensearch-project/documentation-website", "--state", "open", "--label", "v3.9.0"]
    assert not any(call[:2] == ["issue", "comment"] for call in gh.calls)
    assert github.writes == ["comment on 12 (opensearch-project/documentation-website)", "comment on 34 (opensearch-project/documentation-website)"]
    out = capsys.readouterr().out
    assert "Hi @alice" in out and "Hi @bob" in out


def test_doc_prs() -> None:
    gh = FakeGh({"pr list": "https://github.com/opensearch-project/documentation-website/pull/1"})
    chore, _ = chores(["doc-prs", "--action", "check", "--version", "3.9.0-alpha1"], gh)
    chore.run("doc-prs")
    assert "v3.9.0" in gh.calls[0] and '-label:"6 - Done but waiting to merge"' in gh.calls[0]


def test_release_owners_assign() -> None:
    def release(query: Dict[str, Any]) -> Dict[str, Any]:
        if query["_source"] == "release_issue":
            return hits({"release_issue": "https://github.com/opensearch-project/OpenSearch/issues/9"})
        owners = [] if query["query"]["bool"]["filter"][1]["match_phrase"]["component.keyword"] == "OpenSearch" else ["owner"]
        return hits({"release_owners": owners})

    transport = FakeMetrics({release_chores.RELEASE_METRICS_INDEX: release,
                             f"maintainer-inactivity-{release_chores.month_year()}": hits({"github_login": "m1"})})
    gh = FakeGh()
    chore, _ = chores(["release-owners", "--action", "assign", "--manifest", OS_MANIFEST], gh, transport)
    chore.run("release-owners")
    assert gh.calls[0][:3] == ["issue", "comment", "https://github.com/opensearch-project/OpenSearch/issues/9"]
    assert "Hi @m1" in gh.calls[0][4]
    assert gh.calls[1] == ["issue", "edit", "https://github.com/opensearch-project/OpenSearch/issues/9", "--add-assignee", "m1"]


def test_release_issues_create_dispatches_both_workflows() -> None:
    transport = FakeMetrics({release_chores.RELEASE_METRICS_INDEX: hits({"release_issue_exists": False})})
    gh = FakeGh()
    chore, _ = chores(["release-issues", "--action", "create", "--manifest", OS_MANIFEST, "--manifest", OSD_MANIFEST], gh, transport)
    chore.run("release-issues")
    assert gh.calls == [["workflow", "run", "os-release-issues.yml", "-R", "COG-GTM/opensearch-build"],
                        ["workflow", "run", "osd-release-issues.yml", "-R", "COG-GTM/opensearch-build"]]


def test_code_coverage_notify() -> None:
    transport = FakeMetrics({
        f"opensearch-codecov-metrics-{release_chores.month_year()}": hits({"coverage": 0, "state": "no-coverage", "branch": "3.9", "url": "https://codecov.io/x"}),
        release_chores.RELEASE_METRICS_INDEX: hits({"release_issue": "https://github.com/opensearch-project/x/issues/1"}),
    })
    gh = FakeGh()
    chore, _ = chores(["code-coverage", "--action", "notify", "--manifest", OSD_MANIFEST], gh, transport)
    chore.run("code-coverage")
    assert gh.calls and "is not reporting code-coverage for branch [3.9](https://codecov.io/x)" in gh.calls[0][4]


def test_latest_rc_and_build_rc_dispatch(tmp_path: Any) -> None:
    def builds(query: Dict[str, Any]) -> Dict[str, Any]:
        component = query["query"]["bool"]["filter"][0]["match_phrase"]["component"]
        return hits({"rc_number": 3 if component == "OpenSearch" else 2})

    transport = FakeMetrics({release_chores.BUILD_RESULTS_INDEX: builds})
    gh = FakeGh()
    chore, github = chores(["build-rc", "--action", "both", "--version", "3.9.0", "--dry-run"], gh, transport)
    chore.run("build-rc")
    assert gh.calls == []
    assert github.writes == ["dispatch distribution-build-opensearch.yml in COG-GTM/opensearch-build with RC_NUMBER=4",
                             "dispatch distribution-build-opensearch-dashboards.yml in COG-GTM/opensearch-build with RC_NUMBER=3"]
    query = transport.queries[0][1]
    assert {"match_phrase": {"overall_build_result": "SUCCESS"}} in query["query"]["bool"]["filter"]
    assert not any("qualifier" in f["match_phrase"] for f in query["query"]["bool"]["filter"])


def test_build_rc_fails_without_target_workflow_outside_dry_run() -> None:
    transport = FakeMetrics({release_chores.BUILD_RESULTS_INDEX: hits({"rc_number": 1})})
    chore, _ = chores(["build-rc", "--action", "opensearch", "--version", "3.9.0"], FakeGh(), transport)
    with pytest.raises(release_chores.ChoreError, match="distribution-build-opensearch.yml does not exist"):
        chore.run("build-rc")


def test_latest_rc_number_zero_when_no_hits() -> None:
    transport = FakeMetrics({release_chores.BUILD_RESULTS_INDEX: {"hits": {"total": {"value": 0}, "hits": []}}})
    metrics = release_chores.ReleaseMetrics(release_chores.MetricsClient("https://m", transport=transport), "3.0.0", "alpha1")
    assert metrics.latest_rc_number("OpenSearch") == 0
    assert {"match_phrase": {"qualifier": "alpha1"}} in transport.queries[0][1]["query"]["bool"]["filter"]


def test_failed_integ_components_dashboards_clause() -> None:
    transport = FakeMetrics({release_chores.INTEG_RESULTS_INDEX: hits({"component": "a", "component_build_result": "failed"}, {"component": "b", "component_build_result": "passed"})})
    metrics = release_chores.ReleaseMetrics(release_chores.MetricsClient("https://m", transport=transport), "3.9.0")
    assert metrics.failed_integ_components(2, "tar", "x64", ["OpenSearch-Dashboards", "x"]) == ["a"]
    must = transport.queries[0][1]["query"]["bool"]["must"]
    assert not any("terms" in clause for clause in must)
    assert must[-1]["bool"]["should"][0] == {"regexp": {"component": "OpenSearch-Dashboards-ci-group-.*"}}


def test_rc_details_comment() -> None:
    def builds(query: Dict[str, Any]) -> Dict[str, Any]:
        return hits({"rc_number": 5}) if query["_source"] == "rc_number" else hits({"distribution_build_number": 11000})

    transport = FakeMetrics({release_chores.BUILD_RESULTS_INDEX: builds,
                             release_chores.RELEASE_METRICS_INDEX: hits({"release_issue": "https://github.com/opensearch-project/opensearch-build/issues/7"})})
    gh = FakeGh()
    chore, _ = chores(["rc-details", "--action", "add", "--version", "3.9.0"], gh, transport)
    chore.run("rc-details")
    assert gh.calls[0][2] == "https://github.com/opensearch-project/opensearch-build/issues/7"
    assert "opensearchstaging/opensearch:3.9.0.11000" in gh.calls[0][4]


def test_invalid_action() -> None:
    chore, _ = chores(["doc-prs", "--action", "notify", "--version", "3.9.0"], FakeGh())
    with pytest.raises(release_chores.ChoreError, match="Invalid action 'notify'"):
        chore.run("doc-prs")


def test_metrics_client_requires_endpoint() -> None:
    with pytest.raises(release_chores.ChoreError, match="METRICS_CLUSTER_ENDPOINT"):
        release_chores.MetricsClient("")


def test_github_fallback_release_issue_lookup() -> None:
    gh = FakeGh({"search issues": json.dumps([{"url": "https://github.com/opensearch-project/job-scheduler/issues/5", "assignees": [{"login": "o"}]}])})
    metrics = release_chores.GitHubReleaseMetrics(release_chores.GitHub(True, gh), "3.9.0", [OS_MANIFEST])
    assert metrics.release_issue("job-scheduler", "component.keyword") == "https://github.com/opensearch-project/job-scheduler/issues/5"
    assert metrics.release_owners("job-scheduler") == ["o"]
    assert gh.calls[0][:5] == ["search", "issues", "[RELEASE] Release version 3.9.0 in:title", "--repo", "opensearch-project/job-scheduler"]


def test_notes_matrix_skips_components(tmp_path: Any, monkeypatch: Any) -> None:
    output = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    release_chores.notes_matrix(release_chores.parse_args(["notes-matrix", "--manifest", OSD_MANIFEST]))
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    matrix = json.loads(values["matrix"])
    names = [entry["component"] for entry in matrix["include"]]
    assert "functionalTestDashboards" not in names and "OpenSearch-Dashboards" in names
    assert matrix["include"][0]["index"] == 0
    assert int(values["count"]) == len(names)
    with pytest.raises(release_chores.ChoreError):
        release_chores.notes_matrix(release_chores.parse_args(["notes-matrix", "--manifest", OSD_MANIFEST, "--components", "nope"]))


def test_notes_filename() -> None:
    assert release_chores.notes_filename({"name": "OpenSearch", "repository": "x"}, "3.8.0", "opensearch") == "opensearch.release-notes-3.8.0.md"
    plugin = {"name": "job-scheduler", "repository": "https://github.com/opensearch-project/job-scheduler.git"}
    assert release_chores.notes_filename(plugin, "3.8.0", "opensearch") == "opensearch-job-scheduler.release-notes-3.8.0.0.md"


def test_compare_notes() -> None:
    committed = "* a ([#1](https://github.com/o/r/pull/1))\n* b ([#2](https://github.com/o/r/pull/2))\n"
    generated = "* a ([#1](https://github.com/o/r/pull/1))\n* c ([#3](https://github.com/o/r/pull/3))\n"
    summary = release_chores.compare_notes(committed, generated, "x.md")
    assert summary["common_prs"] == ["1"] and summary["only_committed"] == ["2"] and summary["only_generated"] == ["3"]
    assert release_chores.compare_notes(committed, committed, "x.md")["identical"] is True
    assert release_chores.compare_notes(None, generated, "x.md")["committed"] is False


def test_notes_pr_dry_run(tmp_path: Any, capsys: Any) -> None:
    notes = tmp_path / "release-notes"
    notes.mkdir()
    (notes / "opensearch-job-scheduler.release-notes-3.9.0.0.md").write_text("generated\n* x ([#9](https://github.com/o/r/pull/9))\n")
    (notes / "opensearch-job-scheduler.release-notes-3.9.0.0-borderline.md").write_text("- #8 excluded")

    def fake_clone(repository: str, ref: str, destination: str) -> None:
        os.makedirs(os.path.join(destination, "release-notes"))

    gh = FakeGh({"api": "null"})
    args = release_chores.parse_args(["notes-pr", "--manifest", OS_MANIFEST, "--component", "job-scheduler", "--notes-dir", str(notes), "--dry-run"])
    with patch.object(release_chores, "clone", fake_clone):
        release_chores.notes_pr(args, release_chores.GitHub(True, gh))
    out = capsys.readouterr().out
    assert "## Borderline Calls\n- #8 excluded" in out
    assert "No committed release-notes/opensearch-job-scheduler.release-notes-3.9.0.0.md to compare with." in out
    assert "[dry-run] would open PR '[AUTO] Add release notes for 3.9.0' in opensearch-project/job-scheduler (release-chores/release-notes-3.9.0 -> main)" in out
    assert gh.calls == [["api", "repos/opensearch-project/job-scheduler/pulls?head=opensearch-project:release-chores/release-notes-3.9.0&state=open", "--jq", ".[0].number"]]


def test_main_reports_errors(capsys: Any) -> None:
    assert release_chores.main(["doc-prs", "--action", "check"]) == 1
    assert "::error::Version parameter is required." in capsys.readouterr().out
