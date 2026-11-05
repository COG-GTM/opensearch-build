#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Stage plan and run summary for .github/workflows/release-promotion.yml.

``plan`` is the 'Parameters Check' stage of jenkins/release-workflows/release-promotion.jenkinsfile: it checks the
parameters and turns them into the child-workflow inputs every Jenkins stage passes (the ``when`` PROJECTS filters
included). ``summary`` lists every child job of the run, in Jenkins stage order, for ``$GITHUB_STEP_SUMMARY``.
"""

import argparse
import json
import re
import sys
from typing import Any, Dict, List, Optional

PROJECTS = ("both", "opensearch", "opensearch-dashboards")
OS_JOB = "distribution-build-opensearch"
OSD_JOB = "distribution-build-opensearch-dashboards"

# 'Publish' parallel branches, in jenkinsfile order: (stage, job, repo type) for distribution-promote-repos ...
REPO_STAGES = [
    ("OpenSearch Debian Apt promotion", OS_JOB, "apt"),
    ("OpenSearch Yum promotion", OS_JOB, "yum"),
    ("OpenSearch Dashboards Debian Apt promotion", OSD_JOB, "apt"),
    ("OpenSearch Dashboards Yum promotion", OSD_JOB, "yum"),
]
# ... and (stage, job, platform, distribution, architecture) for distribution-promote-artifacts
ARTIFACT_STAGES = [
    ("OpenSearch Windows promotion", OS_JOB, "windows", "zip", "x64"),
    ("OpenSearch Dashboards Windows promotion", OSD_JOB, "windows", "zip", "x64"),
    ("OpenSearch Linux deb arm64", OS_JOB, "linux", "deb", "arm64"),
    ("OpenSearch Linux deb x64", OS_JOB, "linux", "deb", "x64"),
    ("OpenSearch Dashboards Linux deb arm64", OSD_JOB, "linux", "deb", "arm64"),
    ("OpenSearch Dashboards Linux deb x64", OSD_JOB, "linux", "deb", "x64"),
    ("OpenSearch Linux rpm arm64", OS_JOB, "linux", "rpm", "arm64"),
    ("OpenSearch Linux rpm x64", OS_JOB, "linux", "rpm", "x64"),
    ("OpenSearch Dashboards Linux rpm arm64", OSD_JOB, "linux", "rpm", "arm64"),
    ("OpenSearch Dashboards Linux rpm x64", OSD_JOB, "linux", "rpm", "x64"),
    ("OpenSearch Linux tar arm64", OS_JOB, "linux", "tar", "arm64"),
    ("OpenSearch Dashboards Linux tar arm64", OSD_JOB, "linux", "tar", "arm64"),
    ("OpenSearch Dashboards Linux tar x64", OSD_JOB, "linux", "tar", "x64"),
]
TAR_X64_STAGE = "Publish OpenSearch Linux tar x64"
# Every stage, in jenkinsfile order (parallel branches indented under their parent).
STAGES: List[Any] = [
    "Parameters Check",
    "Validate staging artifacts",
    ("Publish", [s[0] for s in REPO_STAGES] + [s[0] for s in ARTIFACT_STAGES]),
    TAR_X64_STAGE,
    ("Invalidate cloudfront and promote docker", ["Invalidate Cloudfront", "Promote Docker"]),
    "Validate production artifacts",
    "Publish to Maven",
]


class ParameterError(Exception):
    pass


def plan(release_version: str, os_rc: str, osd_rc: str, projects: str, tag_docker_latest: bool,
         dry_run: bool = True, production_docker_build_number_override: str = "") -> Dict[str, Any]:
    release_version, os_rc, osd_rc = release_version.strip(), os_rc.strip(), osd_rc.strip()  # string(trim: true)
    if projects not in PROJECTS:
        raise ParameterError(f"PROJECTS must be one of {', '.join(PROJECTS)}, not {projects!r}")
    if not release_version:
        raise ParameterError("Please enter version number for RELEASE_VERSION")
    opensearch = projects in ("both", "opensearch")
    dashboards = projects in ("both", "opensearch-dashboards")
    if opensearch and not os_rc:
        raise ParameterError("Please enter OpenSearch RC Build Number")
    if dashboards and not osd_rc:
        raise ParameterError("Please enter OpenSearch-Dashboards RC Build Number")
    override = production_docker_build_number_override.strip()
    if override and not dry_run:
        raise ParameterError("production-docker-build-number-override is only allowed with dry-run: true")

    def selected(job: str) -> bool:
        return opensearch if job == OS_JOB else dashboards

    def build(job: str) -> str:
        return os_rc if job == OS_JOB else osd_rc

    def manifest(job: str) -> str:
        product = "opensearch" if job == OS_JOB else "opensearch-dashboards"
        return f"{release_version}/{product}-{release_version}.yml"

    repos = [
        {"stage": stage, "job": job, "repo-type": repo_type, "build": build(job), "manifest": manifest(job)}
        for stage, job, repo_type in REPO_STAGES if selected(job)
    ]
    artifacts = [
        {"stage": stage, "job": job, "platform": platform, "distribution": distribution, "architecture": architecture,
         "build": build(job), "manifest": manifest(job)}
        for stage, job, platform, distribution, architecture in ARTIFACT_STAGES if selected(job)
    ]

    # 'Validate staging artifacts' / 'Validate production artifacts': PROJECTS == "opensearch" validates OpenSearch only,
    # anything else validates Both with OSD_BUILD_NUMBER 'latest'.
    only_opensearch = projects == "opensearch"
    validation_options = [f"os-build-number={os_rc}"]
    if not only_opensearch:
        validation_options.append("osd-build-number=latest")

    # Not in Jenkins: lets a dry run of an already released version whose Docker images were re-released since RC
    # validate the production digest gate against another staging tag; empty keeps the Jenkins values above.
    production_options = validation_options
    if override:
        production_options = [f"os-build-number={override}"] + ([] if only_opensearch else [f"osd-build-number={override}"])

    # 'Promote Docker' SOURCE_IMAGES
    images = []
    if opensearch:
        images.append(f"opensearch:{release_version}.{os_rc}")
    if dashboards:
        images.append(f"opensearch-dashboards:{release_version}.{osd_rc}")

    # 'Invalidate Cloudfront': majorVersion = RELEASE_VERSION.find(/\d/), for both products whatever PROJECTS is
    major = re.search(r"\d", release_version)
    major_version = major.group(0) if major else "null"
    cloudfront_paths = [f"/releases/bundle/opensearch/{major_version}.x/*", f"/releases/bundle/opensearch-dashboards/{major_version}.x/*"]

    return {
        "release-version": release_version,
        "opensearch": opensearch,
        "dashboards": dashboards,
        "repos": repos,
        "artifacts": artifacts,
        "os-rc": os_rc,
        "os-manifest": manifest(OS_JOB),
        "validation-projects": "opensearch" if only_opensearch else "Both",
        "staging-validation-options": " ".join(validation_options + ["optional-args=using-staging-artifact-only", "platform=linux,windows"]),
        "production-validation-options": " ".join(production_options + ["optional-args=validate-digest-only", "platform=linux,windows"]),
        "source-images": ", ".join(images),
        "tag-docker-latest": tag_docker_latest,
        "cloudfront-paths": " ".join(cloudfront_paths),
        "production-docker-build-number-override": override,
    }


def plan_markdown(result: Dict[str, Any], dry_run: bool) -> str:
    lines = [
        f"## Release promotion {result['release-version']}{' (dry run)' if dry_run else ''}",
        "",
        "| Stage | Child workflow | Inputs |",
        "| --- | --- | --- |",
        f"| Validate staging artifacts | validate-artifacts | PROJECTS={result['validation-projects']}, ARTIFACT_TYPE=staging, "
        f"DOCKER_SOURCE=dockerhub, OPTIONS=`{result['staging-validation-options']}` |",
    ]
    for repo in result["repos"]:
        lines.append(f"| Publish / {repo['stage']} | distribution-promote-repos | {repo['job']} {repo['repo-type']} build {repo['build']} `{repo['manifest']}` |")
    for art in result["artifacts"]:
        lines.append(
            f"| Publish / {art['stage']} | distribution-promote-artifacts | {art['job']} {art['platform']} {art['distribution']} {art['architecture']} "
            f"build {art['build']} `{art['manifest']}` |"
        )
    if result["opensearch"]:
        lines.append(f"| {TAR_X64_STAGE} | distribution-promote-artifacts | {OS_JOB} linux tar x64 build {result['os-rc']} `{result['os-manifest']}` |")
    lines += [
        f"| Invalidate Cloudfront | (in this workflow) | {result['cloudfront-paths']} |",
        f"| Promote Docker | docker-promotion | SOURCE_IMAGES=`{result['source-images']}`, DOCKER_HUB_PROMOTE=true, ECR_PROMOTE=true, "
        f"TAG_LATEST={str(result['tag-docker-latest']).lower()}, TAG_MAJOR_VERSION=true |",
        f"| Validate production artifacts | validate-artifacts | PROJECTS={result['validation-projects']}, ARTIFACT_TYPE=production, "
        f"DOCKER_SOURCE=Both, OPTIONS=`{result['production-validation-options']}` |",
    ]
    if result["opensearch"]:
        lines.append(f"| Publish to Maven | publish-to-maven ({'plan only' if dry_run else 'dispatched, not awaited'}) | BUILD_ID={result['os-rc']}, VERSION={result['release-version']} |")
    if result["production-docker-build-number-override"]:
        lines += ["", f"> **Production docker digest gate checked against `{result['production-docker-build-number-override']}`, "
                  f"not OpenSearch RC {result['os-rc']}** (production-docker-build-number-override, dry run only)."]
    return "\n".join(lines) + "\n"


SUMMARY_JOB = "Release promotion summary"


def summary(jobs: List[Dict[str, Any]], maven_run_url: str, maven_run_note: str) -> str:
    flat: List[str] = []
    for stage in STAGES:
        flat += [stage[0], *stage[1]] if isinstance(stage, tuple) else [stage]
    # Job names are "<caller job> / <called job>"; when release-promotion is itself called (release-promotion-dry-run),
    # every name gets the caller's prefix, which the summary job's own name reveals.
    prefix = next((job["name"][: -len(SUMMARY_JOB)] for job in jobs if job.get("name", "").endswith(SUMMARY_JOB)), "")
    by_stage: Dict[str, List[Dict[str, str]]] = {}
    for job in jobs:
        name = job.get("name", "")
        if not name.startswith(prefix) or name.endswith(SUMMARY_JOB):
            continue
        stage, _, child = name[len(prefix):].partition(" / ")
        if stage in flat:
            result = job.get("conclusion") or job.get("status") or "unknown"
            by_stage.setdefault(stage, []).append({"child": child or "(this workflow)", "result": result, "url": job.get("html_url", "")})

    def rows(stage: str, indent: str) -> List[str]:
        found = by_stage.get(stage)
        if not found:
            return [f"{indent}- **{stage}**: not run"]
        return [f"{indent}- **{stage}** / [{job['child']}]({job['url']}): `{job['result']}`" for job in sorted(found, key=lambda j: j["child"])]

    lines = ["## Release promotion child runs", ""]
    for stage in STAGES:
        if isinstance(stage, tuple):
            lines.append(f"- **{stage[0]}** (parallel)")
            for branch in stage[1]:
                lines += rows(branch, "  ")
        else:
            lines += rows(stage, "")
            if stage == "Publish to Maven" and maven_run_url:
                lines.append(f"  - dispatched publish-to-maven run: {maven_run_url} {maven_run_note}".rstrip())
    return "\n".join(lines) + "\n"


def _write_outputs(path: Optional[str], values: Dict[str, Any]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in values.items():
            if isinstance(value, bool):
                value = str(value).lower()
            elif not isinstance(value, str):
                value = json.dumps(value, separators=(",", ":"))
            fh.write(f"{key}={value}\n")


def _append(path: Optional[str], text: str) -> None:
    print(text)
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan_cmd = commands.add_parser("plan")
    plan_cmd.add_argument("--release-version", default="")
    plan_cmd.add_argument("--opensearch-rc", default="")
    plan_cmd.add_argument("--opensearch-dashboards-rc", default="")
    plan_cmd.add_argument("--projects", default="both")
    plan_cmd.add_argument("--tag-docker-latest", default="true")
    plan_cmd.add_argument("--dry-run", default="true")
    plan_cmd.add_argument("--production-docker-build-number-override", default="")
    plan_cmd.add_argument("--github-output")
    plan_cmd.add_argument("--summary")
    summary_cmd = commands.add_parser("summary")
    summary_cmd.add_argument("--jobs", required=True, help="JSON file: the run's jobs (GET .../actions/runs/{id}/attempts/{n}/jobs)")
    summary_cmd.add_argument("--maven-run-url", default="")
    summary_cmd.add_argument("--maven-run-note", default="")
    summary_cmd.add_argument("--summary")
    args = parser.parse_args(argv)

    if args.command == "plan":
        try:
            result = plan(args.release_version, args.opensearch_rc, args.opensearch_dashboards_rc, args.projects, args.tag_docker_latest == "true",
                          args.dry_run == "true", args.production_docker_build_number_override)
        except ParameterError as error:
            print(f"::error::{error}")
            return 1
        _write_outputs(args.github_output, result)
        _append(args.summary, plan_markdown(result, args.dry_run == "true"))
        return 0

    with open(args.jobs, encoding="utf-8") as fh:
        data = json.load(fh)
    jobs = data["jobs"] if isinstance(data, dict) else [job for page in data for job in page.get("jobs", [])]
    _append(args.summary, summary(jobs, args.maven_run_url, args.maven_run_note))
    return 0


if __name__ == "__main__":
    sys.exit(main())
