# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import io
import logging
import tarfile
from typing import Any, Dict, Optional

import requests
import yaml

from release_git_workflow.git_remote import tag_commit
from release_git_workflow.release_plan import RefChange, RefPlan
from release_git_workflow.release_version import release_tag

RELEASE_BUNDLE_URL = "https://artifacts.opensearch.org/releases/bundle/{product}/{version}/{product}-{version}-linux-x64.tar.gz"
RC_DIST_MANIFEST_URL = "https://ci.opensearch.org/ci/dbc/distribution-build-{product}/{version}/{build}/linux/x64/tar/dist/{product}/manifest.yml"


def distribution_manifest(product: str, version: str, build: Optional[str] = None) -> Dict[str, Any]:
    """The distribution (bundle) manifest that release-tag.jenkinsfile extracts from the released linux-x64
    tarball; with build, the same manifest of that release candidate on ci.opensearch.org, which allows a
    dry run before the release is published."""
    if build:
        response = requests.get(RC_DIST_MANIFEST_URL.format(product=product, version=version, build=build), timeout=60)
        response.raise_for_status()
        manifest: Dict[str, Any] = yaml.safe_load(response.text)
        return manifest

    member = f"{product}-{version}/manifest.yml"
    with requests.get(RELEASE_BUNDLE_URL.format(product=product, version=version), stream=True, timeout=60) as response:
        response.raise_for_status()
        with tarfile.open(fileobj=response.raw, mode="r|gz") as tar:
            for entry in tar:
                if entry.name == member:
                    extracted = tar.extractfile(entry)
                    if extracted is not None:
                        manifest = yaml.safe_load(io.TextIOWrapper(extracted, encoding="utf-8"))
                        return manifest
    raise ValueError(f"{member} not found in {RELEASE_BUNDLE_URL.format(product=product, version=version)}")


def plan_release_tags(manifest: Dict[str, Any], version: str, plan: Optional[RefPlan] = None) -> RefPlan:
    """Port of createReleaseTag: tag each component's commit_id; skip a tag that already points at it and
    report a conflict when it points elsewhere (the Jenkins job errors in that case)."""
    plan = plan or RefPlan("tag")
    for component in manifest.get("components", []):
        name, repository, commit = component["name"], component["repository"], component["commit_id"]
        tag = release_tag(name, version)
        ref = f"refs/tags/{tag}"
        existing = tag_commit(repository, tag)
        if existing is None:
            plan.changes.append(RefChange(name, repository, ref, commit, "create"))
            logging.info(f"{name}: would create tag {tag} at {commit}.")
        elif existing == commit:
            plan.changes.append(RefChange(name, repository, ref, commit, "exists", "tag already at this commit, skipped"))
        else:
            plan.changes.append(RefChange(name, repository, ref, commit, "conflict", f"tag already exists at {existing}"))
    return plan
