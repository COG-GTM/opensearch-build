# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import logging
import os
from typing import Any, Dict, List
from urllib.parse import urlparse

import requests
import yaml

from release_git_workflow.git_remote import branch_head
from release_git_workflow.release_plan import RefChange, RefPlan

BUILD_MANIFEST_HOSTS = ["ci.opensearch.org", "artifacts.opensearch.org"]


def load_manifest(manifest: str, manifests_dir: str = "manifests") -> Dict[str, Any]:
    """A build manifest URL (any value containing "builds", like the Jenkins job) or an input manifest path
    relative to manifests/, e.g. 3.10.0/opensearch-3.10.0.yml."""
    if "builds" in manifest:
        url = urlparse(manifest)
        if url.scheme != "https" or url.hostname not in BUILD_MANIFEST_HOSTS:
            raise ValueError(f"Build manifests must be https URLs on {', '.join(BUILD_MANIFEST_HOSTS)}: {manifest}")
        response = requests.get(manifest, timeout=60)
        response.raise_for_status()
        data: Dict[str, Any] = yaml.safe_load(response.text)
        return data
    root = os.path.realpath(manifests_dir)
    path = os.path.realpath(os.path.join(root, manifest))
    if os.path.commonpath([root, path]) != root:
        raise ValueError(f"Input manifests must be under {manifests_dir}/: {manifest}")
    with open(path) as f:
        data = yaml.safe_load(f)
        return data


def plan_release_branches(manifests: List[str], source_branch: str, target_branch: str, manifests_dir: str = "manifests") -> RefPlan:
    """Port of release-branch.jenkinsfile: target_branch is created in every component repository that does
    not have it yet, at the build manifest commit_id, or at the source_branch HEAD for input manifests."""
    plan = RefPlan("branch")
    seen: Dict[str, Any] = {}
    for manifest in manifests:
        is_build = "builds" in manifest
        for component in load_manifest(manifest, manifests_dir).get("components", []):
            repository = component.get("repository")
            if not repository:
                # dist components have no repository
                continue
            name = component["name"]
            requested = component.get("commit_id") if is_build else None
            if repository in seen:
                # a repository listed twice is branched once, unless two build manifests disagree on its commit
                if requested and seen[repository] and requested != seen[repository]:
                    plan.changes.append(RefChange(name, repository, f"refs/heads/{target_branch}", requested, "conflict", f"build manifests disagree: {seen[repository]} vs {requested}"))
                continue
            seen[repository] = requested
            ref = f"refs/heads/{target_branch}"
            existing = branch_head(repository, target_branch)
            if existing:
                plan.changes.append(RefChange(name, repository, ref, existing, "exists", "branch already exists, skipped"))
                continue
            commit = component.get("commit_id") if is_build else branch_head(repository, source_branch)
            if not commit:
                source = "commit_id in the build manifest" if is_build else f"source branch {source_branch}"
                plan.changes.append(RefChange(name, repository, ref, None, "conflict", f"no {source}"))
                continue
            plan.changes.append(RefChange(name, repository, ref, commit, "create", "" if is_build else f"from {source_branch}"))
            logging.info(f"{name}: would create {target_branch} at {commit}.")
    return plan
