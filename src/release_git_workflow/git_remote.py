# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import logging
import re
import subprocess
from typing import Dict, Optional, Tuple

import requests

GITHUB_URL = re.compile(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")


def repo_slug(repository: str) -> Tuple[str, str]:
    """("opensearch-project", "OpenSearch") for https://github.com/opensearch-project/OpenSearch.git."""
    match = GITHUB_URL.match(repository.strip())
    if match is None:
        raise ValueError(f"Not a GitHub repository URL: {repository}")
    return match.group(1), match.group(2)


def ls_remote(repository: str, *refs: str) -> Dict[str, str]:
    """Maps each ref the remote has (out of refs) to the object it points at. Reads need no credentials."""
    result = subprocess.run(["git", "ls-remote", repository, *refs], capture_output=True, text=True, check=True, timeout=120)
    found: Dict[str, str] = {}
    for line in result.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if ref in refs:
            found[ref] = sha
    return found


def branch_head(repository: str, branch: str) -> Optional[str]:
    ref = f"refs/heads/{branch}"
    return ls_remote(repository, ref).get(ref)


def tag_commit(repository: str, tag: str) -> Optional[str]:
    """Commit a tag points at, peeling annotated tags."""
    ref = f"refs/tags/{tag}"
    found = ls_remote(repository, ref, f"{ref}^{{}}")
    return found.get(f"{ref}^{{}}", found.get(ref))


class GitHubRefs:
    """Creates branches and lightweight tags through the GitHub REST API with an installation token."""

    API = "https://api.github.com"

    def __init__(self, token: str) -> None:
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})

    def create_ref(self, repository: str, ref: str, sha: str) -> bool:
        """Creates ref at sha; False when it already exists (e.g. created since the plan ran)."""
        owner, name = repo_slug(repository)
        response = self.session.post(f"{self.API}/repos/{owner}/{name}/git/refs", json={"ref": ref, "sha": sha}, timeout=60)
        if response.status_code == 422 and "already exists" in response.text:
            logging.warning(f"{ref} already exists in {owner}/{name}, leaving it unchanged.")
            return False
        if response.status_code != 201:
            raise RuntimeError(f"Creating {ref} at {sha} in {owner}/{name} failed. HTTP status: {response.status_code}: {response.text}")
        logging.info(f"Created {ref} at {sha} in {owner}/{name}.")
        return True
