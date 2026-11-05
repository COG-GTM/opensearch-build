#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Autocut failure issues and webhook notifications for the notify-failure composite action.

Port of opensearch-build-libraries (jenkins@12.0.0): vars/updateBuildFailureIssues.groovy,
vars/updateIntegTestFailureIssues.groovy, vars/createGithubIssue.groovy, vars/closeGithubIssue.groovy,
src/jenkins/CreateIntegTestMarkDownTable.groovy, vars/publishNotification.groovy and, for kind flaky-test,
vars/gradleCheckFlakyTestGitHubIssue.groovy with src/gradlecheck/ParseMarkDownTable.groovy and MarkdownComparator.groovy. Titles, labels, bodies and
the dedupe-by-title lookups are kept byte-for-byte; only the build link points at the GitHub Actions run.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

import yaml

KINDS = ("distribution-build", "integ-test", "flaky-test")
FLAKY_TEST_LABELS = "autocut,>test-failure,flaky-test"
FLAKY_TEST_REPOSITORY = "opensearch-project/OpenSearch"
DAYS_TO_REOPEN = 3
SLEEP_SECONDS = 3
# The Groovy bodies are triple-quoted strings whose continuation lines are indented by 20 spaces; stripIndent()
# keeps that indent because the first line has none, so the issues Jenkins cut today carry it too.
GROOVY_INDENT = " " * 20

METRICS_BASE_URL = "https://metrics.opensearch.org/_dashboards/app/dashboards?security_tenant=global#/view/21aad140-49f6-11ef-bbdd-39a9b324a5aa"
METRICS_QUERY = (
    "?_g=(filters:!(),refreshInterval:(pause:!t,value:0),time:(from:now-30d,to:now))"
    "&_a=(description:'OpenSearch%20Release%20Build%20and%20Integration%20Test%20Results',"
)
METRICS_FILTER = (
    "filters:!(('$state':(store:appState),meta:(alias:!n,disabled:!f,index:d90d2ba0-8fe0-11ef-a168-f19b1bbc360c,key:version,negate:!f,"
    "params:(query:'{version}'),type:phrase),query:(match_phrase:(version:'{version}'))),('$state':(store:appState),meta:(alias:!n,"
    "disabled:!f,index:d90d2ba0-8fe0-11ef-a168-f19b1bbc360c,key:component,negate:!f,params:(query:{component}),type:phrase),"
    "query:(match_phrase:(component:{component})))),fullScreenMode:!f,options:(hidePanelTitles:!f,useMargins:!t),"
    "query:(language:kuery,query:''),timeRestore:!t,title:'OpenSearch%20Release%20Build%20and%20Integration%20Test%20Results',viewMode:view)"
)
METRICS_PANELS = {
    "tar_x64": "ddafb9c5-2d35-482a-9c61-1ba78b67f406",
    "tar_arm64": "c570bdfd-3122-4e31-a02d-2130d797d9fc",
    "deb_x64": "5743d5c4-be75-49b9-a81f-fef3f805ad99",
    "deb_arm64": "7a6ee111-1c99-4f96-9a3f-c0f248181980",
    "rpm_x64": "94f0246f-4246-4f05-ba11-b3e22836b8e7",
    "rpm_arm64": "eae6bad4-cffc-4672-a688-14155229ea63",
    "windows_x64": "a57afb35-8d97-4641-9b07-64ff614dab00",
}
INTEG_ROW_FIELDS = ("platform", "distribution", "architecture", "distribution_build_number", "rc_number", "test_report_manifest_yml", "integ_test_build_url")


@dataclass
class IssueAction:
    action: str  # "open" (create, edit or reopen) or "close"
    component: str
    repo: str
    title: str
    body: str
    labels: List[str] = field(default_factory=list)
    # "recent": reopen an issue closed in the last DAYS_TO_REOPEN days; "table-changed": reopen the closed issue only
    # when its table rows differ (gradleCheckFlakyTestGitHubIssue)
    reopen: str = "recent"


def groovy_str(value: Any) -> str:
    """Render a value the way Groovy GString interpolation does (null -> 'null')."""
    return "null" if value is None else str(value)


def repo_path(repo_url: str) -> str:
    """closeGithubIssue.groovy: https://github.com/<owner>/<repo>.git -> <owner>/<repo>."""
    path = re.sub(r"^https://github\.com/", "", repo_url.strip())
    return re.sub(r"\.git$", "", path).strip("/")


def issue_title(kind: str, component: str, version: str) -> str:
    if kind == "distribution-build":
        return f"[AUTOCUT] Distribution Build Failed for {component}-{version}"
    return f"[AUTOCUT] Integration Test Failed for {component}-{version}"


def issue_labels(version: str) -> List[str]:
    """createGithubIssue(label: "autocut,v<version>") plus the 'untriaged' label it always adds."""
    return ["autocut", f"v{version}", "untriaged"]


def build_failure_body(component: str, version: str, run_url: str) -> str:
    return (
        f"***Build Failed Error***: **{component} failed during the distribution build for version: {version}.**\n"
        f"{GROOVY_INDENT}Please see build log at {run_url}.\n"
        f"{GROOVY_INDENT}The failed build stage will be marked as unstable :warning: . Please see ./build.sh step for more details.\n"
        f"{GROOVY_INDENT}Checkout the [wiki](https://github.com/opensearch-project/opensearch-build/wiki/Building-an-OpenSearch-and-OpenSearch-Dashboards-Distribution) "
        "to reproduce the failure locally."
    )


def build_close_comment(component: str, version: str, run_url: str) -> str:
    return f"Closing the issue as the distribution build for {component} has passed for version: **{version}**.\n{GROOVY_INDENT}Please see build log at {run_url}"


def integ_close_comment(component: str, version: str) -> str:
    return f"Closing the issue as the integration tests for {component} passed for version: **{version}**."


def metrics_visualization_url(distribution: Any, architecture: Any, version: str, component: str) -> Optional[str]:
    panel = METRICS_PANELS.get(f"{distribution}_{architecture}")
    if not panel:
        return None
    return METRICS_BASE_URL + METRICS_QUERY + f"expandedPanelId:{panel}," + METRICS_FILTER.format(version=version, component=component)


def integ_failure_body(component: str, version: str, rows: List[Dict[str, Any]], run_url: str, release_owners: List[str]) -> str:
    """CreateIntegTestMarkDownTable.create(); a row without integ_test_build_url links the current run."""
    header = (
        f"\n### Integration Test Failed for version {version}. See the specifications below:\n\n"
        "#### Details\n\n"
        "| Platform | Dist | Arch | Dist Build No. | RC | Test Report | Workflow Run | Failing tests |\n"
        "|----------|------|------|----------------|----|-------------|--------------|---------------|\n"
    )
    lines = []
    for row in rows:
        values = {key: row.get(key) for key in INTEG_ROW_FIELDS}
        values["integ_test_build_url"] = values["integ_test_build_url"] or run_url
        metrics = metrics_visualization_url(row.get("distribution"), row.get("architecture"), version, component)
        cells = " | ".join(groovy_str(values[key]) for key in INTEG_ROW_FIELDS)
        lines.append(f"| {cells} | [Check metrics]({groovy_str(metrics)}) |")
    info = (
        "\n\nCheck out test report manifest linked above for steps to reproduce, cluster and integration test failure logs. "
        "For additional information checkout the [wiki](https://github.com/opensearch-project/opensearch-build/wiki/Testing-the-Distribution) "
        "and [OpenSearch Metrics Dashboard](https://metrics.opensearch.org/_dashboards/app/dashboards#/view/21aad140-49f6-11ef-bbdd-39a9b324a5aa).\n"
    )
    if release_owners:
        info += "\nTagging the release owners to take a look " + " ".join(f"@{owner}" for owner in release_owners)
    return header + "\n".join(lines) + info


def group_failures(failures: List[Any], component: Optional[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Failure list -> {component: [detail rows]}.

    With `component` set every entry belongs to it and the component counts as failed even when the list is empty.
    Without it each entry is a component name or an object with a `component` key.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    if component:
        grouped[component] = [entry for entry in failures if isinstance(entry, dict)]
        return grouped
    for entry in failures:
        if isinstance(entry, str):
            grouped.setdefault(entry, [])
        elif isinstance(entry, dict) and entry.get("component"):
            grouped.setdefault(str(entry["component"]), []).append(entry)
        else:
            raise ValueError(f"Failure entry needs a component: {entry!r}")
    return grouped


def plan_issues(
    kind: str,
    manifest: Dict[str, Any],
    version: str,
    run_url: str,
    failures: Dict[str, List[Dict[str, Any]]],
    passed: List[str],
    release_owners: Dict[str, List[str]],
) -> List[IssueAction]:
    repositories = {c["name"]: c["repository"] for c in manifest.get("components") or []}
    actions: List[IssueAction] = []
    for name, rows in failures.items():
        if name not in repositories:
            print(f"Warning: {name} is not a component of the input manifest, skipping", file=sys.stderr)
            continue
        if kind == "integ-test" and not rows:
            # updateIntegTestFailureIssues.groovy skips a component with no failed rows (flaky, latest run passed).
            print(f"Warning: no failed integration test rows for {name}, skipping issue update", file=sys.stderr)
            continue
        if kind == "distribution-build":
            body = build_failure_body(name, version, run_url)
        else:
            body = integ_failure_body(name, version, rows, run_url, release_owners.get(name) or [])
        actions.append(IssueAction("open", name, repo_path(repositories[name]), issue_title(kind, name, version), body, issue_labels(version)))
    for name in dict.fromkeys(passed):
        if name in failures or name not in repositories:
            continue
        comment = build_close_comment(name, version, run_url) if kind == "distribution-build" else integ_close_comment(name, version)
        actions.append(IssueAction("close", name, repo_path(repositories[name]), issue_title(kind, name, version), comment))
    return actions


def plan_flaky_issues(issues: List[Dict[str, Any]], repository: str, labels: str) -> List[IssueAction]:
    """gradleCheckFlakyTestGitHubIssue(label: "<labels>") plus the 'untriaged' label it always adds."""
    label_list = [lbl.strip() for lbl in (labels or FLAKY_TEST_LABELS).split(",") if lbl.strip()] + ["untriaged"]
    repo = repo_path(repository or FLAKY_TEST_REPOSITORY)
    return [
        IssueAction("open", str(issue.get("test_class") or issue["title"]), repo, issue["title"], issue["body"], label_list, "table-changed")
        for issue in issues
    ]


def parse_table_rows(markdown: str) -> List[Dict[str, str]]:
    """ParseMarkDownTable.parseMarkdownTableRows(): skip the header and separator rows and the trailing text."""
    rows = re.split(r"\|\s*\n", markdown or "")[2:-1]
    parsed = []
    for row in rows:
        cells = re.split(r"\|\s*", row)
        if len(cells) < 5:
            continue
        parsed.append({key: cells[index].strip() for index, key in enumerate(("Git Reference", "Merged Pull Request", "Build Details", "Test Name"), start=1)})
    return parsed


def table_differences(new_body: str, existing_body: str) -> List[Dict[str, str]]:
    """MarkdownComparator.markdownComparison(): rows of the new report missing from the closed issue."""
    existing = parse_table_rows(existing_body)
    return [row for row in parse_table_rows(new_body) if row not in existing]


Runner = Callable[[List[str]], str]


def run_gh(args: List[str]) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout.strip()


def find_issue(gh: Runner, repo: str, title: str, state: str = "open", extra_query: str = "") -> str:
    query = f"{title} in:title{extra_query}"
    out = gh(["issue", "list", "--repo", repo, "--state", state, "-S", query, "--json", "number,title"])
    # Full-text search also matches longer titles (notifications vs notifications-core); keep exact title matches only.
    matches = [issue for issue in json.loads(out or "[]") if issue.get("title") == title]
    return str(matches[0]["number"]) if matches else ""


def apply_issue(action: IssueAction, gh: Runner, today: date) -> str:
    """createGithubIssue(issueEdit: true) / closeGithubIssue(); returns what was done."""
    open_issue = find_issue(gh, action.repo, action.title)
    if action.action == "close":
        if not open_issue:
            print(f"No open AUTOCUT issue to close for {action.component} in {action.repo}")
            return "none"
        gh(["issue", "close", open_issue, "-R", action.repo, "--comment", action.body])
        return f"closed #{open_issue}"
    if open_issue:
        gh(["issue", "edit", open_issue, "--repo", action.repo, "--body", action.body])
        return f"edited #{open_issue}"
    if action.reopen == "table-changed":
        closed_issue = find_issue(gh, action.repo, action.title, "closed")
        if closed_issue:
            existing_body = gh(["issue", "view", closed_issue, "--repo", action.repo, "--json", "body", "--jq", ".body"])
            differences = table_differences(action.body, existing_body)
            if not differences:
                print("Not Re-opening the issue as the no change in the Flaky report after the issue is closed")
                return f"skipped closed #{closed_issue} (report unchanged)"
            for row in differences:
                print(f"Git Reference: {row['Git Reference']}, Merged Pull Request: {row['Merged Pull Request']}, "
                      f"Build Details: {row['Build Details']}, Test Name: {row['Test Name']}")
            gh(["issue", "reopen", "--repo", action.repo, closed_issue])
            gh(["issue", "edit", closed_issue, "--repo", action.repo, "--body", action.body])
            return f"reopened #{closed_issue}"
        closed_issue = ""
    else:
        since = (today - timedelta(days=DAYS_TO_REOPEN)).strftime("%Y-%m-%d")
        closed_issue = find_issue(gh, action.repo, action.title, "closed", f" closed:>={since}")
    if closed_issue:
        gh(["issue", "reopen", "--repo", action.repo, closed_issue])
        gh(["issue", "edit", closed_issue, "--repo", action.repo, "--body", action.body])
        return f"reopened #{closed_issue}"
    for label in action.labels:
        if gh(["label", "list", "--repo", action.repo, "-S", label, "--json", "name", "--jq", ".[0].name"]) != label:
            gh(["label", "create", label, "--repo", action.repo])
    label_args = ["--label", ",".join(lbl for lbl in action.labels if lbl != "untriaged"), "--label", "untriaged"]
    return "created " + gh(["issue", "create", "--title", action.title, "--body", action.body, *label_args, "--repo", action.repo])


def open_autocut_issues(gh: Runner, repo: str) -> Dict[str, str]:
    """Open [AUTOCUT] issues of a repository, title -> number (read-only, for dry runs)."""
    out = gh(["issue", "list", "--repo", repo, "--state", "open", "-S", "[AUTOCUT] in:title", "--limit", "1000", "--json", "number,title"])
    return {issue["title"]: str(issue["number"]) for issue in json.loads(out or "[]")}


def dry_run_outcome(action: IssueAction, open_issues: Optional[Dict[str, str]]) -> str:
    if open_issues is None:
        return ""
    number = open_issues.get(action.title)
    if action.action == "close":
        return f"close open #{number}" if number else "nothing to close (no open issue)"
    if number:
        return f"edit open #{number}"
    return "reopen the closed issue if its report changed, else create" if action.reopen == "table-changed" else "reopen a recently closed issue, else create"


def print_dry_run(action: IssueAction, outcome: str = "") -> None:
    print(f"::group::[dry-run] {action.action} {action.repo}: {action.title}")
    print(f"repository: {action.repo}")
    print(f"title: {action.title}")
    if outcome:
        print(f"against open issues: {outcome}")
    if action.action == "open":
        print(f"labels: {', '.join(action.labels)}")
        if action.reopen == "table-changed":
            print("dedupe: edit the open issue with this title, else reopen the closed one when its table rows changed, else create")
        else:
            print("dedupe: edit the open issue with this title, else reopen one closed in the last "
                  f"{DAYS_TO_REOPEN} days, else create")
        print("body:")
    else:
        print("close comment:")
    print(action.body)
    print("::endgroup::")


def print_dry_run_plan(actions: List[IssueAction], gh: Optional[Runner]) -> None:
    open_issues: Dict[str, Optional[Dict[str, str]]] = {}
    for repo in dict.fromkeys(a.repo for a in actions):
        try:
            open_issues[repo] = open_autocut_issues(gh, repo) if gh else None
        except subprocess.CalledProcessError as ex:
            print(f"::warning::Unable to list the open AUTOCUT issues of {repo}: {ex.stderr or ex}", file=sys.stderr)
            open_issues[repo] = None
    outcomes = [dry_run_outcome(action, open_issues[action.repo]) for action in actions]
    for action, outcome in zip(actions, outcomes):
        print_dry_run(action, outcome)
    print(f"[dry-run] {sum(a.action == 'open' for a in actions)} issue(s) to open or update, {sum(a.action == 'close' for a in actions)} to close")
    if gh:
        print("| Repository | Title | Against open issues |")
        print("|------------|-------|---------------------|")
        for action, outcome in zip(actions, outcomes):
            print(f"| {action.repo} | {action.title} | {outcome or 'lookup failed'} |")


def webhook_text(icon: str, job_name: str, run_number: str, message: str, run_url: str, manifest: str, extra: str) -> str:
    """publishNotification.groovy result_text."""
    parts = [icon, f"JOB_NAME={job_name}", f"BUILD_NUMBER=[{run_number}]", f"MESSAGE={message}", f"BUILD_URL: {run_url}"]
    if manifest:
        parts.append(f"MANIFEST: {manifest}")
    if extra:
        parts.append(extra)
    return "\n".join(parts)


def post_webhook(url: str, text: str) -> int:
    data = json.dumps({"result_text": text}).encode()
    request = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 - URL comes from a repository secret
        return int(response.status)


def load_json_list(value: str) -> List[Any]:
    value = (value or "").strip()
    if not value:
        return []
    if value.startswith("["):
        loaded = json.loads(value)
        if not isinstance(loaded, list):
            raise ValueError("Expected a JSON list")
        return loaded
    return [line.strip() for line in re.split(r"[\n,]", value) if line.strip()]


def failure_summary(failures: Dict[str, List[Dict[str, Any]]]) -> str:
    lines = []
    for name, rows in failures.items():
        platforms = sorted({f"{r.get('platform')}-{r.get('distribution')}-{r.get('architecture')}" for r in rows})
        lines.append(f"{name}: {', '.join(platforms)}" if platforms else name)
    return "\n".join(lines)


def write_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as output:
            output.write(f"{name}={value}\n")


def main(argv: Optional[List[str]] = None, gh: Runner = run_gh, today: Optional[date] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["repos", "issues", "webhook"])
    parser.add_argument("--kind", choices=KINDS + ("none",), default="none")
    parser.add_argument("--input-manifest", default="")
    parser.add_argument("--component", default="")
    parser.add_argument("--version", default="")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--failures", default="", help="JSON list or newline/comma separated component names")
    parser.add_argument("--passed", default="", help="Components whose AUTOCUT issue is closed")
    parser.add_argument("--release-owners", default="", help='JSON object {"<component>": ["<login>", ...]}')
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--lookup-existing", action="store_true", help="Dry run: read the open AUTOCUT issues and say what each update would do")
    parser.add_argument("--issues-file", default="", help='flaky-test: JSON list [{"test_class", "title", "body"}, ...]')
    parser.add_argument("--repository", default="", help=f"flaky-test: repository the issues are filed in (default {FLAKY_TEST_REPOSITORY})")
    parser.add_argument("--labels", default="", help=f"flaky-test: comma separated labels (default {FLAKY_TEST_LABELS})")
    parser.add_argument("--job-name", default="")
    parser.add_argument("--run-number", default="")
    parser.add_argument("--message", default="")
    parser.add_argument("--icon", default=":warning:")
    parser.add_argument("--webhook-manifest", default="")
    args = parser.parse_args(argv)

    failures = group_failures(load_json_list(args.failures), args.component or None)

    if args.command == "webhook":
        text = webhook_text(args.icon, args.job_name, args.run_number, args.message, args.run_url, args.webhook_manifest, failure_summary(failures))
        url = os.environ.get("WEBHOOK_URL", "")
        if args.dry_run or not url:
            print(f"[dry-run] webhook payload{'' if url else ' (no WEBHOOK_URL set)'}:")
            print(json.dumps({"result_text": text}, indent=2))
            return 0
        print(f"Webhook responded {post_webhook(url, text)}")
        return 0

    if args.kind == "none":
        return 0
    if args.kind == "flaky-test":
        with open(args.issues_file) as issues_file:
            actions = plan_flaky_issues(json.load(issues_file), args.repository, args.labels)
    else:
        with open(args.input_manifest) as manifest_file:
            manifest = yaml.safe_load(manifest_file)
        version = args.version or str(manifest["build"]["version"])
        owners = json.loads(args.release_owners) if args.release_owners.strip() else {}
        actions = plan_issues(args.kind, manifest, version, args.run_url, failures, load_json_list(args.passed), owners)

    if args.command == "repos":
        repos = sorted({a.repo for a in actions})
        owner_names = sorted({r.split("/")[0] for r in repos})
        if len(owner_names) > 1:
            print(f"One GitHub App token covers one owner, got {owner_names}", file=sys.stderr)
            return 1
        write_output("owner", owner_names[0] if owner_names else "")
        write_output("repositories", ",".join(r.split("/", 1)[1] for r in repos))
        print("\n".join(repos))
        return 0

    if args.dry_run:
        print_dry_run_plan(actions, gh if args.lookup_existing else None)
        return 0

    errors = 0
    for index, action in enumerate(actions):
        if index:
            time.sleep(SLEEP_SECONDS)
        try:
            print(f"{action.component}: {apply_issue(action, gh, today or date.today())}")
        except subprocess.CalledProcessError as ex:
            errors += 1
            print(f"::error::Unable to update the GitHub issue for {action.repo}: {ex.stderr or ex}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
