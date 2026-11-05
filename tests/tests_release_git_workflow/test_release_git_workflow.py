# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import yaml

from release_git_workflow.git_remote import GitHubRefs, branch_head, repo_slug, tag_commit
from release_git_workflow.release_branch import plan_release_branches
from release_git_workflow.release_manifest_lock import ReleaseManifestLock
from release_git_workflow.release_plan import RefChange, RefPlan
from release_git_workflow.release_tag import plan_release_tags
from release_git_workflow.release_version import release_branch, release_tag
from release_git_workflow.run_plan import apply_plan

OS_REPO = "https://github.com/opensearch-project/OpenSearch.git"
JS_REPO = "https://github.com/opensearch-project/job-scheduler.git"


class TestReleaseVersion(unittest.TestCase):
    def test_release_tag(self) -> None:
        self.assertEqual("3.10.0", release_tag("OpenSearch", "3.10.0"))
        self.assertEqual("3.10.0", release_tag("functionalTestDashboards", "3.10.0"))
        self.assertEqual("3.10.0.0", release_tag("job-scheduler", "3.10.0"))
        self.assertEqual("3.0.0.0-alpha1", release_tag("job-scheduler", "3.0.0-alpha1"))
        self.assertEqual("3.0.0-alpha1", release_tag("OpenSearch-Dashboards", "3.0.0-alpha1"))

    def test_release_branch(self) -> None:
        self.assertEqual("3.10", release_branch("3.10.0"))
        self.assertEqual("3.0", release_branch("3.0.0-alpha1"))


class TestGitRemote(unittest.TestCase):
    def test_repo_slug(self) -> None:
        self.assertEqual(("opensearch-project", "OpenSearch"), repo_slug(OS_REPO))
        self.assertEqual(("COG-GTM", "opensearch-build"), repo_slug("https://github.com/COG-GTM/opensearch-build"))
        with self.assertRaises(ValueError):
            repo_slug("https://gitlab.com/a/b.git")

    @patch("release_git_workflow.git_remote.subprocess.run")
    def test_branch_head_matches_exact_ref(self, run: MagicMock) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, stdout="aaa\trefs/heads/3.10\n")
        self.assertEqual("aaa", branch_head(OS_REPO, "3.10"))
        run.assert_called_once_with(["git", "ls-remote", OS_REPO, "refs/heads/3.10"], capture_output=True, text=True, check=True, timeout=120)
        run.return_value = subprocess.CompletedProcess([], 0, stdout="")
        self.assertIsNone(branch_head(OS_REPO, "3.10"))

    @patch("release_git_workflow.git_remote.subprocess.run")
    def test_tag_commit_peels_annotated_tags(self, run: MagicMock) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, stdout="tagobj\trefs/tags/3.10.0\ncommit\trefs/tags/3.10.0^{}\n")
        self.assertEqual("commit", tag_commit(OS_REPO, "3.10.0"))
        run.return_value = subprocess.CompletedProcess([], 0, stdout="light\trefs/tags/3.10.0\n")
        self.assertEqual("light", tag_commit(OS_REPO, "3.10.0"))

    def test_create_ref(self) -> None:
        refs = GitHubRefs("token")
        refs.session = MagicMock()
        refs.session.post.return_value = MagicMock(status_code=201)
        self.assertTrue(refs.create_ref(OS_REPO, "refs/tags/3.10.0", "abc"))
        refs.session.post.assert_called_once_with(
            "https://api.github.com/repos/opensearch-project/OpenSearch/git/refs", json={"ref": "refs/tags/3.10.0", "sha": "abc"}, timeout=60
        )
        refs.session.post.return_value = MagicMock(status_code=422, text="Reference already exists")
        self.assertFalse(refs.create_ref(OS_REPO, "refs/tags/3.10.0", "abc"))
        refs.session.post.return_value = MagicMock(status_code=403, text="denied")
        with self.assertRaisesRegex(RuntimeError, "HTTP status: 403"):
            refs.create_ref(OS_REPO, "refs/tags/3.10.0", "abc")


class TestReleaseBranch(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.dir.name, "3.10.0"))
        manifest = {
            "components": [
                {"name": "OpenSearch", "repository": OS_REPO, "ref": "main"},
                {"name": "job-scheduler", "repository": JS_REPO, "ref": "main"},
                {"name": "dist-only", "dist": "https://x"},
            ]
        }
        with open(os.path.join(self.dir.name, "3.10.0", "opensearch-3.10.0.yml"), "w") as f:
            yaml.safe_dump(manifest, f)

    def tearDown(self) -> None:
        self.dir.cleanup()

    @patch("release_git_workflow.release_branch.branch_head")
    def test_input_manifest_branches_from_source_head(self, head: MagicMock) -> None:
        heads = {(OS_REPO, "3.10"): "existing", (OS_REPO, "main"): "os-main", (JS_REPO, "main"): "js-main"}
        head.side_effect = lambda repository, branch: heads.get((repository, branch))
        plan = plan_release_branches(["3.10.0/opensearch-3.10.0.yml"], "main", "3.10", self.dir.name)
        self.assertEqual([("OpenSearch", "exists", "existing"), ("job-scheduler", "create", "js-main")], [(c.component, c.action, c.commit) for c in plan.changes])
        self.assertEqual({"opensearch-project": ["job-scheduler"]}, plan.owner_and_repositories())

    @patch("release_git_workflow.release_branch.branch_head", return_value=None)
    def test_missing_source_branch_is_a_conflict(self, head: MagicMock) -> None:
        plan = plan_release_branches(["3.10.0/opensearch-3.10.0.yml"], "nope", "3.10", self.dir.name)
        self.assertEqual(2, len(plan.conflicts))

    @patch("release_git_workflow.release_branch.branch_head", return_value=None)
    @patch("release_git_workflow.release_branch.requests.get")
    def test_build_manifest_branches_from_commit_id(self, get: MagicMock, head: MagicMock) -> None:
        get.return_value = MagicMock(text=yaml.safe_dump({"components": [{"name": "OpenSearch", "repository": OS_REPO, "commit_id": "c1"}]}))
        plan = plan_release_branches(["https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/3.10.0/1/linux/x64/tar/builds/opensearch/manifest.yml"], "main", "3.10")
        self.assertEqual([("refs/heads/3.10", "c1", "create")], [(c.ref, c.commit, c.action) for c in plan.changes])


class TestReleaseTag(unittest.TestCase):
    MANIFEST = {"components": [{"name": "OpenSearch", "repository": OS_REPO, "commit_id": "c1"}, {"name": "job-scheduler", "repository": JS_REPO, "commit_id": "c2"}]}

    @patch("release_git_workflow.release_tag.tag_commit")
    def test_plan(self, tag: MagicMock) -> None:
        tag.side_effect = lambda repository, name: {(OS_REPO, "3.10.0"): "c1"}.get((repository, name))
        plan = plan_release_tags(self.MANIFEST, "3.10.0")
        self.assertEqual([("refs/tags/3.10.0", "exists"), ("refs/tags/3.10.0.0", "create")], [(c.ref, c.action) for c in plan.changes])

    @patch("release_git_workflow.release_tag.tag_commit", return_value="other")
    def test_tag_at_other_commit_is_a_conflict(self, tag: MagicMock) -> None:
        plan = plan_release_tags(self.MANIFEST, "3.10.0")
        self.assertEqual(2, len(plan.conflicts))
        with self.assertRaisesRegex(RuntimeError, "conflict"):
            apply_plan(plan)


class TestRefPlan(unittest.TestCase):
    def test_json_round_trip_and_markdown(self) -> None:
        plan = RefPlan("tag", [RefChange("OpenSearch", OS_REPO, "refs/tags/3.10.0", "c1", "create")])
        restored = RefPlan.from_json(plan.to_json())
        self.assertEqual(plan.changes, restored.changes)
        self.assertEqual({"opensearch-project": ["OpenSearch"]}, json.loads(plan.to_json())["scope"])
        self.assertIn("| OpenSearch | {} | `refs/tags/3.10.0` | `c1` | create |".format(OS_REPO), plan.to_markdown("t"))

    @patch.dict(os.environ, {"GITHUB_TOKEN": "t"})
    @patch("release_git_workflow.run_plan.GitHubRefs")
    def test_apply_plan_creates_only_planned_refs(self, refs: MagicMock) -> None:
        refs.return_value.create_ref.return_value = True
        plan = RefPlan("branch", [RefChange("a", OS_REPO, "refs/heads/3.10", "c1", "create"), RefChange("b", JS_REPO, "refs/heads/3.10", "c2", "exists")])
        self.assertEqual(1, apply_plan(plan))
        refs.return_value.create_ref.assert_called_once_with(OS_REPO, "refs/heads/3.10", "c1")


class TestReleaseManifestLock(unittest.TestCase):
    CONTENT = """---
schema-version: '1.2'
build:
  name: {name}
  version: 3.10.0
components:
  - name: {core}
    repository: https://github.com/opensearch-project/{core}.git
    ref: main
  - name: {plugin}
    repository: https://github.com/opensearch-project/{plugin}.git
    ref: main
    platforms:
      - linux
"""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.dir.name, "3.10.0"))
        self.write("opensearch", name="OpenSearch", core="OpenSearch", plugin="job-scheduler")
        self.write("opensearch-dashboards", name="OpenSearch Dashboards", core="OpenSearch-Dashboards", plugin="functionalTestDashboards")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def write(self, product: str, **values: str) -> None:
        with open(os.path.join(self.dir.name, "3.10.0", f"{product}-3.10.0.yml"), "w") as f:
            f.write(self.CONTENT.format(**values))

    def read(self, product: str) -> str:
        with open(os.path.join(self.dir.name, "3.10.0", f"{product}-3.10.0.yml")) as f:
            return f.read()

    def test_update_to_tags_keeps_formatting(self) -> None:
        changes = ReleaseManifestLock("3.10.0", "UPDATE_TO_TAGS", [], {}, self.dir.name).run()
        self.assertEqual(["tags/3.10.0", "tags/3.10.0.0", "tags/3.10.0", "tags/3.10.0"], [c.new_ref for c in changes])
        expected = self.CONTENT.format(name="OpenSearch", core="OpenSearch", plugin="job-scheduler").replace("ref: main\n  - name: job", "ref: tags/3.10.0\n  - name: job")
        self.assertEqual(expected.replace("ref: main", "ref: tags/3.10.0.0"), self.read("opensearch"))

    def test_update_to_release_branch_with_component_filter(self) -> None:
        changes = ReleaseManifestLock("3.10.0", "UPDATE_TO_RELEASE_BRANCH", ["job-scheduler", " functionalTestDashboards"], {}, self.dir.name).run()
        self.assertEqual([("job-scheduler", "main", "3.10"), ("functionalTestDashboards", "main", "3.10")], [(c.component, c.old_ref, c.new_ref) for c in changes])

    @patch("release_git_workflow.release_manifest_lock.branch_head", return_value="head")
    def test_update_to_recent_commits_skips_functional_test_dashboards(self, head: MagicMock) -> None:
        changes = ReleaseManifestLock("3.10.0", "UPDATE_TO_RECENT_COMMITS", [], {}, self.dir.name).run()
        self.assertEqual(["OpenSearch", "job-scheduler", "OpenSearch-Dashboards"], [c.component for c in changes])
        head.assert_any_call("https://github.com/opensearch-project/OpenSearch.git", "3.10")

    @patch("release_git_workflow.release_manifest_lock.requests.get")
    def test_match_build_manifest(self, get: MagicMock) -> None:
        get.return_value = MagicMock(text=yaml.safe_dump({"components": [{"name": "OpenSearch", "commit_id": "c1"}, {"name": "functionalTestDashboards", "commit_id": "c9"}]}))
        changes = ReleaseManifestLock("3.10.0", "MATCH_BUILD_MANIFEST", [], {"opensearch": "12331", "opensearch-dashboards": "9130"}, self.dir.name).run()
        self.assertEqual([("OpenSearch", "c1")], [(c.component, c.new_ref) for c in changes])
        get.assert_any_call("https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/3.10.0/12331/linux/x64/tar/dist/opensearch/manifest.yml", timeout=60)

    def test_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown manifest lock action"):
            ReleaseManifestLock("3.10.0", "NOPE", [], {}, self.dir.name)
        with self.assertRaisesRegex(ValueError, "release candidate build numbers"):
            ReleaseManifestLock("3.10.0", "MATCH_BUILD_MANIFEST", [], {"opensearch": "1"}, self.dir.name)
        with self.assertRaisesRegex(FileNotFoundError, "add the 3.11.0 input manifests"):
            ReleaseManifestLock("3.11.0", "UPDATE_TO_TAGS", [], {}, self.dir.name).run()
