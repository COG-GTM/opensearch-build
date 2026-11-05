# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import json
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


@dataclass
class RefChange:
    """One ref the workflow would write (action "create"), already has ("exists"), or cannot write ("conflict")."""

    component: str
    repository: str
    ref: str
    commit: Optional[str]
    action: str
    detail: str = ""


@dataclass
class RefPlan:
    kind: str
    changes: List[RefChange] = field(default_factory=list)

    @property
    def to_create(self) -> List[RefChange]:
        return [change for change in self.changes if change.action == "create"]

    @property
    def conflicts(self) -> List[RefChange]:
        return [change for change in self.changes if change.action == "conflict"]

    def owner_and_repositories(self) -> Dict[str, List[str]]:
        """Repositories that get a write, grouped by owner, for the GitHub App token scope."""
        from release_git_workflow.git_remote import repo_slug

        scope: Dict[str, List[str]] = {}
        for change in self.to_create:
            owner, name = repo_slug(change.repository)
            if name not in scope.setdefault(owner, []):
                scope[owner].append(name)
        return scope

    def to_json(self) -> str:
        return json.dumps({"kind": self.kind, "changes": [asdict(change) for change in self.changes], "scope": self.owner_and_repositories()}, indent=2)

    @classmethod
    def from_json(cls, content: str) -> "RefPlan":
        data = json.loads(content)
        return cls(data["kind"], [RefChange(**change) for change in data["changes"]])

    def to_markdown(self, title: str) -> str:
        lines = [f"### {title}", "", f"{len(self.to_create)} to create, {len(self.changes) - len(self.to_create) - len(self.conflicts)} already present, {len(self.conflicts)} conflict(s).", ""]
        lines += [f"| Component | Repository | {self.kind.capitalize()} | Commit | Action |", "| --- | --- | --- | --- | --- |"]
        for change in self.changes:
            action = f"{change.action}: {change.detail}" if change.detail else change.action
            lines.append(f"| {change.component} | {change.repository} | `{change.ref}` | `{change.commit or '-'}` | {action} |")
        return "\n".join(lines) + "\n"
