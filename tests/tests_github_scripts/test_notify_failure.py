# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from datetime import date
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts", "notify_failure.py")
SPEC = importlib.util.spec_from_file_location("notify_failure", SCRIPT)
notify_failure: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(notify_failure)

MANIFEST = os.path.join(os.path.dirname(__file__), "..", "..", "manifests", "3.9.0", "opensearch-3.9.0.yml")
RUN_URL = "https://github.com/opensearch-project/opensearch-build/actions/runs/42"
INDENT = " " * 20
ROW = {
    "platform": "linux",
    "distribution": "tar",
    "architecture": "x64",
    "distribution_build_number": "11111",
    "rc_number": "0",
    "test_report_manifest_yml": "https://ci.opensearch.org/test-report.yml",
    "integ_test_build_url": "https://github.com/opensearch-project/opensearch-build/actions/runs/7",
}


TITLE = "[AUTOCUT] Distribution Build Failed for sql-3.9.0"


def issues(*pairs: Any) -> str:
    return json.dumps([{"number": number, "title": title} for number, title in pairs])


class FakeGh:
    def __init__(self, responses: Dict[str, str] = None, fail_on: str = None) -> None:
        self.calls: List[List[str]] = []
        self.responses = responses or {}
        self.fail_on = fail_on

    def __call__(self, args: List[str]) -> str:
        self.calls.append(args)
        joined = " ".join(args)
        if self.fail_on and self.fail_on in joined:
            raise subprocess.CalledProcessError(1, ["gh", *args], stderr="boom")
        for needle, response in self.responses.items():
            if needle in joined:
                return response
        return ""

    def commands(self) -> List[str]:
        return [" ".join(call[:2]) for call in self.calls]


def open_action(kind: str = "distribution-build", body: str = "body") -> Any:
    return notify_failure.IssueAction("open", "sql", "opensearch-project/sql", notify_failure.issue_title(kind, "sql", "3.9.0"), body, notify_failure.issue_labels("3.9.0"))


class TestRendering(unittest.TestCase):
    def test_titles_and_labels_match_jenkins(self) -> None:
        self.assertEqual(notify_failure.issue_title("distribution-build", "sql", "3.9.0"), "[AUTOCUT] Distribution Build Failed for sql-3.9.0")
        self.assertEqual(notify_failure.issue_title("integ-test", "sql", "3.9.0"), "[AUTOCUT] Integration Test Failed for sql-3.9.0")
        self.assertEqual(notify_failure.issue_labels("3.9.0"), ["autocut", "v3.9.0", "untriaged"])

    def test_build_failure_body_matches_jenkins(self) -> None:
        self.assertEqual(
            notify_failure.build_failure_body("sql", "3.9.0", RUN_URL),
            "***Build Failed Error***: **sql failed during the distribution build for version: 3.9.0.**\n"
            f"{INDENT}Please see build log at {RUN_URL}.\n"
            f"{INDENT}The failed build stage will be marked as unstable :warning: . Please see ./build.sh step for more details.\n"
            f"{INDENT}Checkout the [wiki](https://github.com/opensearch-project/opensearch-build/wiki/Building-an-OpenSearch-and-OpenSearch-Dashboards-Distribution)"
            " to reproduce the failure locally.",
        )

    def test_close_comments_match_jenkins(self) -> None:
        self.assertEqual(
            notify_failure.build_close_comment("sql", "3.9.0", RUN_URL),
            f"Closing the issue as the distribution build for sql has passed for version: **3.9.0**.\n{INDENT}Please see build log at {RUN_URL}",
        )
        self.assertEqual(notify_failure.integ_close_comment("sql", "3.9.0"), "Closing the issue as the integration tests for sql passed for version: **3.9.0**.")

    def test_integ_failure_body(self) -> None:
        body = notify_failure.integ_failure_body("sql", "3.9.0", [ROW, dict(ROW, distribution="zip", architecture="x64", integ_test_build_url=None)], RUN_URL, ["alice", "bob"])
        lines = body.split("\n")
        self.assertEqual(lines[:6], [
            "",
            "### Integration Test Failed for version 3.9.0. See the specifications below:",
            "",
            "#### Details",
            "",
            "| Platform | Dist | Arch | Dist Build No. | RC | Test Report | Workflow Run | Failing tests |",
        ])
        self.assertTrue(lines[7].startswith(f"| linux | tar | x64 | 11111 | 0 | https://ci.opensearch.org/test-report.yml | {ROW['integ_test_build_url']} | [Check metrics](https://metrics"))
        self.assertIn("expandedPanelId:ddafb9c5-2d35-482a-9c61-1ba78b67f406,", lines[7])
        self.assertIn("params:(query:'3.9.0')", lines[7])
        self.assertIn("query:(match_phrase:(component:sql))", lines[7])
        self.assertEqual(lines[8], f"| linux | zip | x64 | 11111 | 0 | https://ci.opensearch.org/test-report.yml | {RUN_URL} | [Check metrics](null) |")
        self.assertEqual(lines[9], "")
        self.assertTrue(lines[10].startswith("Check out test report manifest linked above"))
        self.assertEqual(body.split("\n")[-1], "Tagging the release owners to take a look @alice @bob")

    def test_integ_failure_body_without_owners(self) -> None:
        body = notify_failure.integ_failure_body("sql", "3.9.0", [ROW], RUN_URL, [])
        self.assertTrue(body.endswith("21aad140-49f6-11ef-bbdd-39a9b324a5aa).\n"))
        self.assertNotIn("Tagging", body)

    def test_repo_path(self) -> None:
        self.assertEqual(notify_failure.repo_path("https://github.com/opensearch-project/sql.git"), "opensearch-project/sql")
        self.assertEqual(notify_failure.repo_path("opensearch-project/sql"), "opensearch-project/sql")

    def test_webhook_text_matches_publish_notification(self) -> None:
        self.assertEqual(
            notify_failure.webhook_text(":warning:", "bwc-test", "7", "Failed", RUN_URL, "manifest.yml", "sql"),
            f":warning:\nJOB_NAME=bwc-test\nBUILD_NUMBER=[7]\nMESSAGE=Failed\nBUILD_URL: {RUN_URL}\nMANIFEST: manifest.yml\nsql",
        )
        self.assertEqual(notify_failure.webhook_text(":white_check_mark:", "j", "1", "ok", RUN_URL, "", ""), f":white_check_mark:\nJOB_NAME=j\nBUILD_NUMBER=[1]\nMESSAGE=ok\nBUILD_URL: {RUN_URL}")


class TestInputs(unittest.TestCase):
    def test_load_json_list(self) -> None:
        self.assertEqual(notify_failure.load_json_list(""), [])
        self.assertEqual(notify_failure.load_json_list("sql\nk-NN, security\n"), ["sql", "k-NN", "security"])
        self.assertEqual(notify_failure.load_json_list('["sql", {"component": "k-NN"}]'), ["sql", {"component": "k-NN"}])
        with pytest.raises(ValueError):
            notify_failure.load_json_list('[1')
        with pytest.raises(ValueError):
            notify_failure.load_json_list('["a"')

    def test_group_failures(self) -> None:
        self.assertEqual(notify_failure.group_failures(["sql", dict(ROW, component="k-NN"), dict(ROW, component="k-NN")], None), {"sql": [], "k-NN": [dict(ROW, component="k-NN")] * 2})
        self.assertEqual(notify_failure.group_failures([ROW, "ignored"], "sql"), {"sql": [ROW]})
        self.assertEqual(notify_failure.group_failures([], "sql"), {"sql": []})
        with pytest.raises(ValueError):
            notify_failure.group_failures([{"platform": "linux"}], None)

    def test_failure_summary(self) -> None:
        self.assertEqual(notify_failure.failure_summary({"sql": [ROW], "k-NN": []}), "sql: linux-tar-x64\nk-NN")


class TestPlan(unittest.TestCase):
    manifest = {"components": [
        {"name": "sql", "repository": "https://github.com/opensearch-project/sql.git"},
        {"name": "k-NN", "repository": "https://github.com/opensearch-project/k-NN.git"},
        {"name": "security", "repository": "https://github.com/opensearch-project/security.git"},
    ]}

    def test_distribution_build_plan(self) -> None:
        actions = notify_failure.plan_issues("distribution-build", self.manifest, "3.9.0", RUN_URL, {"sql": [], "unknown": []}, ["sql", "k-NN", "k-NN"], {})
        self.assertEqual([(a.action, a.component, a.repo) for a in actions], [("open", "sql", "opensearch-project/sql"), ("close", "k-NN", "opensearch-project/k-NN")])
        self.assertEqual(actions[0].body, notify_failure.build_failure_body("sql", "3.9.0", RUN_URL))
        self.assertEqual(actions[1].body, notify_failure.build_close_comment("k-NN", "3.9.0", RUN_URL))

    def test_integ_test_plan_skips_components_without_rows(self) -> None:
        actions = notify_failure.plan_issues("integ-test", self.manifest, "3.9.0", RUN_URL, {"sql": [ROW], "k-NN": []}, ["security"], {"sql": ["alice"]})
        self.assertEqual([(a.action, a.component) for a in actions], [("open", "sql"), ("close", "security")])
        self.assertTrue(actions[0].body.endswith("@alice"))
        self.assertEqual(actions[1].body, notify_failure.integ_close_comment("security", "3.9.0"))


class TestApplyIssue(unittest.TestCase):
    today = date(2026, 11, 5)

    def test_edits_open_issue(self) -> None:
        gh = FakeGh({"--state open": issues((12, TITLE))})
        self.assertEqual(notify_failure.apply_issue(open_action(), gh, self.today), "edited #12")
        query = f"{TITLE} in:title"
        self.assertEqual(gh.calls[0], ["issue", "list", "--repo", "opensearch-project/sql", "--state", "open", "-S", query, "--json", "number,title"])
        self.assertEqual(gh.calls[-1], ["issue", "edit", "12", "--repo", "opensearch-project/sql", "--body", "body"])

    def test_reopens_recently_closed_issue(self) -> None:
        gh = FakeGh({"--state closed": issues((9, TITLE))})
        self.assertEqual(notify_failure.apply_issue(open_action(), gh, self.today), "reopened #9")
        self.assertEqual(gh.calls[1][4:8], ["--state", "closed", "-S", f"{TITLE} in:title closed:>=2026-11-02"])
        self.assertEqual(gh.commands(), ["issue list", "issue list", "issue reopen", "issue edit"])

    def test_creates_issue_and_missing_labels(self) -> None:
        gh = FakeGh({"-S autocut --json name": "autocut", "issue create": "https://github.com/opensearch-project/sql/issues/1"})
        self.assertEqual(notify_failure.apply_issue(open_action(), gh, self.today), "created https://github.com/opensearch-project/sql/issues/1")
        self.assertIn(["label", "create", "v3.9.0", "--repo", "opensearch-project/sql"], gh.calls)
        self.assertNotIn(["label", "create", "autocut", "--repo", "opensearch-project/sql"], gh.calls)
        self.assertEqual(gh.calls[-1], [
            "issue", "create", "--title", "[AUTOCUT] Distribution Build Failed for sql-3.9.0", "--body", "body",
            "--label", "autocut,v3.9.0", "--label", "untriaged", "--repo", "opensearch-project/sql",
        ])

    def test_ignores_issues_with_a_longer_title(self) -> None:
        similar = TITLE.replace("sql-", "sql-jdbc-")
        gh = FakeGh({"--state open": issues((7, similar)), "--state closed": issues((8, similar)), "issue create": "created-url"})
        self.assertEqual(notify_failure.apply_issue(open_action(), gh, self.today), "created created-url")
        self.assertNotIn("issue edit", gh.commands())

    def test_closes_open_issue(self) -> None:
        action = notify_failure.IssueAction("close", "sql", "opensearch-project/sql", "[AUTOCUT] Distribution Build Failed for sql-3.9.0", "comment")
        gh = FakeGh({"--state open": issues((5, TITLE))})
        self.assertEqual(notify_failure.apply_issue(action, gh, self.today), "closed #5")
        self.assertEqual(gh.calls[-1], ["issue", "close", "5", "-R", "opensearch-project/sql", "--comment", "comment"])

    def test_close_without_open_issue_is_noop(self) -> None:
        action = notify_failure.IssueAction("close", "sql", "opensearch-project/sql", "t", "comment")
        gh = FakeGh({"--state open": "[]"})
        self.assertEqual(notify_failure.apply_issue(action, gh, self.today), "none")
        self.assertEqual(gh.commands(), ["issue list"])


class TestMain(unittest.TestCase):
    def test_dry_run_prints_issue_without_calling_gh(self) -> None:
        gh = FakeGh()
        with patch("builtins.print") as mock_print:
            args = ["issues", "--kind", "distribution-build", "--input-manifest", MANIFEST, "--failures", "sql", "--passed", "k-NN", "--run-url", RUN_URL, "--dry-run"]
            code = notify_failure.main(args, gh=gh)
        output = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertEqual(code, 0)
        self.assertEqual(gh.calls, [])
        self.assertIn("title: [AUTOCUT] Distribution Build Failed for sql-3.9.0", output)
        self.assertIn("labels: autocut, v3.9.0, untriaged", output)
        self.assertIn(notify_failure.build_failure_body("sql", "3.9.0", RUN_URL), output)
        self.assertIn("close comment:", output)

    @patch("time.sleep")
    def test_issues_applies_every_action_and_reports_failures(self, mock_sleep: MagicMock) -> None:
        gh = FakeGh({"for sql-3.9.0 in:title": issues((3, notify_failure.issue_title("integ-test", "sql", "3.9.0"))),
                     "for k-NN-3.9.0 in:title": issues((3, notify_failure.issue_title("integ-test", "k-NN", "3.9.0")))}, fail_on="issue edit")
        args = ["issues", "--kind", "integ-test", "--input-manifest", MANIFEST, "--component", "sql", "--failures", json.dumps([ROW]), "--passed", "k-NN", "--run-url", RUN_URL]
        code = notify_failure.main(args, gh=gh, today=date(2026, 11, 5))
        self.assertEqual(code, 1)
        self.assertIn(["issue", "close", "3", "-R", "opensearch-project/k-NN", "--comment", notify_failure.integ_close_comment("k-NN", "3.9.0")], gh.calls)
        mock_sleep.assert_called_once_with(3)

    def test_kind_none_is_noop(self) -> None:
        self.assertEqual(notify_failure.main(["issues"], gh=FakeGh()), 0)

    def test_repos_writes_token_scope(self) -> None:
        output = os.path.join(self._tmp(), "output")
        with patch.dict(os.environ, {"GITHUB_OUTPUT": output}):
            code = notify_failure.main(["repos", "--kind", "distribution-build", "--input-manifest", MANIFEST, "--failures", "sql,k-NN", "--passed", "security"])
        self.assertEqual(code, 0)
        with open(output) as f:
            self.assertEqual(f.read(), "owner=opensearch-project\nrepositories=k-NN,security,sql\n")

    def test_repos_rejects_multiple_owners(self) -> None:
        manifest = os.path.join(self._tmp(), "manifest.yml")
        with open(manifest, "w") as f:
            f.write("build: {version: 1.0.0}\ncomponents:\n  - {name: a, repository: https://github.com/one/a.git}\n  - {name: b, repository: https://github.com/two/b.git}\n")
        with patch.dict(os.environ, {"GITHUB_OUTPUT": ""}):
            self.assertEqual(notify_failure.main(["repos", "--kind", "distribution-build", "--input-manifest", manifest, "--failures", "a,b"]), 1)

    def test_webhook_dry_run_without_url(self) -> None:
        with patch.dict(os.environ, {"WEBHOOK_URL": ""}), patch("builtins.print") as mock_print, patch.object(notify_failure, "post_webhook") as mock_post:
            code = notify_failure.main(["webhook", "--component", "sql", "--failures", json.dumps([ROW]), "--job-name", "bwc-test", "--run-number", "7", "--message", "Failed", "--run-url", RUN_URL])
        self.assertEqual(code, 0)
        mock_post.assert_not_called()
        payload = json.loads(mock_print.call_args_list[-1].args[0])
        self.assertEqual(payload["result_text"], f":warning:\nJOB_NAME=bwc-test\nBUILD_NUMBER=[7]\nMESSAGE=Failed\nBUILD_URL: {RUN_URL}\nsql: linux-tar-x64")

    def test_webhook_posts_when_url_set(self) -> None:
        with patch.dict(os.environ, {"WEBHOOK_URL": "https://hooks.example.com/x"}), patch.object(notify_failure, "post_webhook", return_value=200) as mock_post:
            code = notify_failure.main(["webhook", "--message", "ok", "--icon", ":white_check_mark:", "--run-url", RUN_URL])
        self.assertEqual(code, 0)
        mock_post.assert_called_once_with("https://hooks.example.com/x", f":white_check_mark:\nJOB_NAME=\nBUILD_NUMBER=[]\nMESSAGE=ok\nBUILD_URL: {RUN_URL}")

    @patch("urllib.request.urlopen")
    def test_post_webhook_sends_result_text_json(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value.__enter__.return_value.status = 200
        self.assertEqual(notify_failure.post_webhook("https://hooks.example.com/x", "line1\nline2"), 200)
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(request.data), {"result_text": "line1\nline2"})

    def _tmp(self) -> str:
        import tempfile
        path = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(path, ignore_errors=True))
        return path


FLAKY_TITLE = "[AUTOCUT] Gradle Check Flaky Test Report for FooIT"
FLAKY_HEADER = (
    "   \n## Flaky Test Report for `FooIT`\n\n### Details\n\n"
    "| Git Reference | Merged Pull Request | Build Details | Test Name |\n"
    "|---------------|----------------------|---------------|-----------|\n"
)
FLAKY_FOOTER = "\n\nThe other pull requests are:\n\n- [1](https://github.com/opensearch-project/OpenSearch/pull/1)\n"
FLAKY_BODY = FLAKY_HEADER + "| abc | [7](pr/7) | [9](build/9) | `testA` |\n| def | [8](pr/8) | [10](build/10) | `testB` |" + FLAKY_FOOTER


def flaky_action(body: str = FLAKY_BODY) -> Any:
    return notify_failure.plan_flaky_issues([{"test_class": "FooIT", "title": FLAKY_TITLE, "body": body}], "", "")[0]


class TestFlakyTestIssues(unittest.TestCase):
    def test_plan_defaults_match_jenkins(self) -> None:
        action = flaky_action()
        self.assertEqual(action.repo, "opensearch-project/OpenSearch")
        self.assertEqual(action.labels, ["autocut", ">test-failure", "flaky-test", "untriaged"])
        self.assertEqual(action.reopen, "table-changed")
        self.assertEqual(action.component, "FooIT")
        custom = notify_failure.plan_flaky_issues([{"title": "t", "body": "b"}], "https://github.com/o/r.git", "a, b")[0]
        self.assertEqual((custom.repo, custom.labels, custom.component), ("o/r", ["a", "b", "untriaged"], "t"))

    def test_parse_table_rows(self) -> None:
        self.assertEqual(notify_failure.parse_table_rows(FLAKY_BODY), [
            {"Git Reference": "abc", "Merged Pull Request": "[7](pr/7)", "Build Details": "[9](build/9)", "Test Name": "`testA`"},
            {"Git Reference": "def", "Merged Pull Request": "[8](pr/8)", "Build Details": "[10](build/10)", "Test Name": "`testB`"},
        ])
        self.assertEqual(notify_failure.parse_table_rows(""), [])

    def test_table_differences(self) -> None:
        older = FLAKY_HEADER + "| abc | [7](pr/7) | [9](build/9) | `testA` |" + FLAKY_FOOTER
        self.assertEqual(notify_failure.table_differences(older, FLAKY_BODY), [])
        self.assertEqual([row["Git Reference"] for row in notify_failure.table_differences(FLAKY_BODY, older)], ["def"])

    def test_edits_open_issue(self) -> None:
        gh = FakeGh({"--state open": issues((5, FLAKY_TITLE))})
        self.assertEqual(notify_failure.apply_issue(flaky_action(), gh, date(2026, 11, 5)), "edited #5")
        self.assertEqual(gh.calls[-1], ["issue", "edit", "5", "--repo", "opensearch-project/OpenSearch", "--body", FLAKY_BODY])

    def test_does_not_reopen_unchanged_report(self) -> None:
        gh = FakeGh({"--state closed": issues((6, FLAKY_TITLE)), "issue view": FLAKY_BODY})
        self.assertEqual(notify_failure.apply_issue(flaky_action(), gh, date(2026, 11, 5)), "skipped closed #6 (report unchanged)")
        self.assertNotIn("issue reopen", gh.commands())
        closed_search = [c for c in gh.calls if "--state" in c and c[c.index("--state") + 1] == "closed"][0]
        self.assertNotIn("closed:>=", " ".join(closed_search))

    def test_reopens_changed_report(self) -> None:
        older = FLAKY_HEADER + "| abc | [7](pr/7) | [9](build/9) | `testA` |" + FLAKY_FOOTER
        gh = FakeGh({"--state closed": issues((6, FLAKY_TITLE)), "issue view": older})
        with patch("builtins.print"):
            self.assertEqual(notify_failure.apply_issue(flaky_action(), gh, date(2026, 11, 5)), "reopened #6")
        self.assertEqual(gh.commands()[-2:], ["issue reopen", "issue edit"])

    def test_creates_issue_with_labels(self) -> None:
        gh = FakeGh({"label list": ">test-failure"})
        result = notify_failure.apply_issue(flaky_action(), gh, date(2026, 11, 5))
        self.assertTrue(result.startswith("created"))
        create = gh.calls[-1]
        self.assertEqual(create[:5], ["issue", "create", "--title", FLAKY_TITLE, "--body"])
        self.assertEqual(create[6:], ["--label", "autocut,>test-failure,flaky-test", "--label", "untriaged", "--repo", "opensearch-project/OpenSearch"])

    def test_main_reads_issues_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "issues.json")
            with open(path, "w") as f:
                json.dump([{"test_class": "FooIT", "title": FLAKY_TITLE, "body": FLAKY_BODY}], f)
            output = os.path.join(tmp, "out")
            with patch.dict(os.environ, {"GITHUB_OUTPUT": output}), patch("builtins.print"):
                self.assertEqual(notify_failure.main(["repos", "--kind", "flaky-test", "--issues-file", path], gh=FakeGh()), 0)
            with open(output) as f:
                self.assertEqual(f.read(), "owner=opensearch-project\nrepositories=OpenSearch\n")
            gh = FakeGh({"issue list": issues((5, FLAKY_TITLE), (9, "[AUTOCUT] Gradle Check Flaky Test Report for BarIT"))})
            with patch("builtins.print") as mock_print:
                code = notify_failure.main(["issues", "--kind", "flaky-test", "--issues-file", path, "--dry-run", "--lookup-existing"], gh=gh)
            printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertEqual(code, 0)
        self.assertEqual(gh.commands(), ["issue list"])
        self.assertIn("against open issues: edit open #5", printed)
        self.assertIn(f"| opensearch-project/OpenSearch | {FLAKY_TITLE} | edit open #5 |", printed)
        self.assertIn("reopen the closed one when its table rows changed", printed)


class TestDryRunLookup(unittest.TestCase):
    def test_outcomes(self) -> None:
        close = notify_failure.IssueAction("close", "sql", "o/sql", TITLE, "c")
        self.assertEqual(notify_failure.dry_run_outcome(open_action(), None), "")
        self.assertEqual(notify_failure.dry_run_outcome(open_action(), {TITLE: "3"}), "edit open #3")
        self.assertEqual(notify_failure.dry_run_outcome(open_action(), {}), "reopen a recently closed issue, else create")
        self.assertEqual(notify_failure.dry_run_outcome(close, {TITLE: "4"}), "close open #4")
        self.assertEqual(notify_failure.dry_run_outcome(close, {}), "nothing to close (no open issue)")

    def test_lookup_failure_is_a_warning(self) -> None:
        gh = FakeGh(fail_on="issue list")
        with patch("builtins.print") as mock_print:
            code = notify_failure.main(["issues", "--kind", "distribution-build", "--input-manifest", MANIFEST, "--failures", "sql", "--dry-run", "--lookup-existing"], gh=gh)
        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertEqual(code, 0)
        self.assertIn("lookup failed", printed)
