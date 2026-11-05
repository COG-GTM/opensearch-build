# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import logging
import os
from typing import Optional

from release_git_workflow.git_remote import GitHubRefs
from release_git_workflow.release_plan import RefPlan


def write_outputs(plan: RefPlan, title: str, output: Optional[str], summary: Optional[str]) -> None:
    if output:
        with open(output, "w") as f:
            f.write(plan.to_json())
    markdown = plan.to_markdown(title)
    if summary:
        with open(summary, "a") as f:
            f.write(markdown + "\n")
    print(markdown)


def apply_plan(plan: RefPlan) -> int:
    """Creates every ref the plan marks "create" with the token in GITHUB_TOKEN; returns how many were written."""
    if plan.conflicts:
        raise RuntimeError(f"Refusing to write: {len(plan.conflicts)} conflict(s) in the plan.")
    refs = GitHubRefs(os.environ["GITHUB_TOKEN"])
    created = sum(1 for change in plan.to_create if refs.create_ref(change.repository, change.ref, str(change.commit)))
    logging.info(f"Created {created} of {len(plan.to_create)} planned {plan.kind}(es).")
    return created
