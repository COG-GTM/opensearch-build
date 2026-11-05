# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests
import ruamel.yaml
import yaml

from release_git_workflow.git_remote import branch_head
from release_git_workflow.release_tag import RC_DIST_MANIFEST_URL
from release_git_workflow.release_version import PRODUCTS, release_branch, release_tag

ACTIONS = ["MATCH_BUILD_MANIFEST", "UPDATE_TO_RECENT_COMMITS", "UPDATE_TO_TAGS", "UPDATE_TO_RELEASE_BRANCH"]
UNLOCKED_COMPONENTS = ["functionalTestDashboards"]


@dataclass
class LockChange:
    manifest: str
    component: str
    old_ref: str
    new_ref: str


class ReleaseManifestLock:
    """Port of release-manifest-commit-lock-lf.jenkinsfile: rewrites the ref of the selected components in
    manifests/<version>/{opensearch,opensearch-dashboards}-<version>.yml.

    MATCH_BUILD_MANIFEST     commit_id from the release candidate build (functionalTestDashboards excluded)
    UPDATE_TO_RECENT_COMMITS HEAD of the release branch, e.g. 3.10 (functionalTestDashboards excluded)
    UPDATE_TO_TAGS           tags/<release tag>, the tags release-tag creates
    UPDATE_TO_RELEASE_BRANCH the release branch name
    """

    def __init__(self, version: str, action: str, components: List[str], release_candidates: Dict[str, str], manifests_dir: str = "manifests") -> None:
        if action not in ACTIONS:
            raise ValueError(f"Unknown manifest lock action {action}, expected one of {', '.join(ACTIONS)}.")
        if action == "MATCH_BUILD_MANIFEST" and not all(release_candidates.get(product) for product in PRODUCTS):
            raise ValueError("MATCH_BUILD_MANIFEST needs both the OpenSearch and the OpenSearch Dashboards release candidate build numbers.")
        self.version = version
        self.action = action
        self.components = [component.strip() for component in components if component.strip()]
        self.release_candidates = release_candidates
        self.manifests_dir = manifests_dir
        self.yaml = ruamel.yaml.YAML()
        self.yaml.explicit_start = True  # type: ignore
        self.yaml.preserve_quotes = True  # type: ignore
        self.yaml.indent(mapping=2, sequence=4, offset=2)

    def manifest_path(self, product: str) -> str:
        return os.path.join(self.manifests_dir, self.version, f"{product}-{self.version}.yml")

    def run(self) -> List[LockChange]:
        changes: List[LockChange] = []
        for product in PRODUCTS:
            path = self.manifest_path(product)
            if not os.path.exists(path):
                raise FileNotFoundError(f"{path} does not exist; add the {self.version} input manifests before locking them.")
            with open(path) as f:
                data = self.yaml.load(f)

            build_commits = self.__build_commits(product) if self.action == "MATCH_BUILD_MANIFEST" else {}
            product_changes: List[LockChange] = []
            for component in data.get("components", []):
                name = component["name"]
                if "repository" not in component or (self.components and name not in self.components):
                    continue
                new_ref = self.__new_ref(name, component["repository"], build_commits)
                if new_ref and new_ref != component.get("ref"):
                    product_changes.append(LockChange(path, name, str(component.get("ref")), new_ref))
                    component["ref"] = new_ref

            if product_changes:
                with open(path, "w") as f:
                    self.yaml.dump(data, f)
                logging.info(f"Locked {len(product_changes)} component(s) in {path}.")
            changes.extend(product_changes)
        return changes

    def __new_ref(self, name: str, repository: str, build_commits: Dict[str, str]) -> Optional[str]:
        if self.action == "MATCH_BUILD_MANIFEST":
            return None if name in UNLOCKED_COMPONENTS else build_commits.get(name)
        if self.action == "UPDATE_TO_RECENT_COMMITS":
            return None if name in UNLOCKED_COMPONENTS else branch_head(repository, release_branch(self.version))
        if self.action == "UPDATE_TO_TAGS":
            return f"tags/{release_tag(name, self.version)}"
        return release_branch(self.version)

    def __build_commits(self, product: str) -> Dict[str, str]:
        url = RC_DIST_MANIFEST_URL.format(product=product, version=self.version, build=self.release_candidates[product])
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        manifest: Dict[str, Any] = yaml.safe_load(response.text)
        return {component["name"]: component["commit_id"] for component in manifest.get("components", [])}
