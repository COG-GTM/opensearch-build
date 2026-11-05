#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Promotion plans for the release-promotion building blocks (s5.3).

Each subcommand reproduces what one opensearch-build-libraries step promotes, so the GHA workflows can show it in a
dry run and use the same rules for the real run:

* ``artifacts``: vars/promoteArtifacts.groovy (jenkins@12.0.0), staging S3 -> ``releases/{plugins,core,bundle}/``.
* ``repos``: vars/promoteRepos.groovy (jenkins@12.0.0), the yum/apt repositories under ``releases/bundle/<f>/<n>.x/``.
* ``docker``: vars/promoteContainer.groovy (lf-jenkins), the docker-copy calls to Docker Hub and ECR Public.
* ``maven``: publish-to-maven-lf.jenkinsfile + vars/publishToMaven.groovy (lf-jenkins), the Maven Central coordinates.

A dry run builds the staging file list from the public build manifests (``PUBLIC_ARTIFACT_URL``), so it needs no AWS
access. ``--verify`` compares every planned destination with what is published (artifacts.opensearch.org, the yum/apt
metadata, the registries, Maven Central). ``artifacts --local-root`` builds the upload list from the files actually
downloaded and signed, which is what the real run uploads.
"""

import argparse
import fnmatch
import gzip
import json
import os
import posixpath
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import yaml

DEFAULT_PUBLIC_ARTIFACT_URL = "https://ci.opensearch.org/ci/dbc"
DEFAULT_PRODUCTION_URL = "https://artifacts.opensearch.org"
# Google's Maven Central mirror: repo1.maven.org answers bursts of HEAD requests with 429
MAVEN_CENTRAL_URL = "https://maven-central.storage-download.googleapis.com/maven2"
# createSha512Checksums.groovy allowedFileTypes
SHA512_TYPES = (".tar.gz", ".zip", ".rpm", ".deb")
# src/sign_workflow/signer_pgp.py SignerPGP.ACCEPTED_FILE_TYPES (sign.sh --platform linux)
PGP_TYPES = (".zip", ".jar", ".war", ".pom", ".module", ".tar.gz", ".whl", ".crate", ".rpm", ".deb", ".tgz")
DIST_TYPES = {"yum": "rpm", "apt": "deb"}
PLATFORM_DISTRIBUTIONS = {"linux": ["tar", "rpm", "deb"], "windows": ["zip"]}
DOCKER_PRODUCTION = "opensearchproject"
ECR_PRODUCTION = "public.ecr.aws/opensearchorg/opensearchproject"
USER_AGENT = "opensearch-build-release-promotion-plan"

Fetch = Callable[[str, str], Tuple[int, bytes, Dict[str, str]]]


# ---------------------------------------------------------------------------------------------------------------
# HTTP


def http(url: str, method: str = "GET", headers: Optional[Dict[str, str]] = None, retries: int = 5) -> Tuple[int, bytes, Dict[str, str]]:
    """GET/HEAD with backoff on 429/5xx. Returns (status, body, headers); 4xx other than 429 are returned, not raised."""
    delay = 2.0
    for attempt in range(retries):
        request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # nosec B310 - https URLs built here
                return response.status, response.read(), {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as error:
            if error.code != 429 and error.code < 500:
                return error.code, b"", {k.lower(): v for k, v in error.headers.items()}
            if attempt == retries - 1:
                return error.code, b"", {}
        except urllib.error.URLError:
            if attempt == retries - 1:
                raise
        time.sleep(delay)
        delay *= 2
    return 0, b"", {}


def fetch_yaml(url: str) -> Dict[str, Any]:
    status, body, _ = http(url)
    if status != 200:
        raise SystemExit(f"GET {url} returned {status}")
    loaded: Dict[str, Any] = yaml.safe_load(body)
    return loaded


def exists(url: str) -> bool:
    return http(url, method="HEAD")[0] == 200


# ---------------------------------------------------------------------------------------------------------------
# Input manifest (lib.jenkins.InputManifest)


def manifest_facts(manifest: Dict[str, Any]) -> Dict[str, str]:
    build = manifest["build"]
    version = str(build["version"])
    qualifier = build.get("qualifier")
    major = version.split(".")[0]
    return {
        "filename": str(build.get("filename") or build["name"]).lower().replace(" ", "-"),
        "version": version,
        "revision": version + (f"-{qualifier}" if qualifier else ""),
        "major": major,
        "repo-version": f"{major}.x",
        # promoteArtifacts / promoteRepos / publish-to-maven-lf: majorVersion > 2 -> release@opensearch.org
        "signing-email": "release@opensearch.org" if int(major) > 2 else "opensearch@amazon.com",
    }


def load_facts(path: str) -> Dict[str, str]:
    with open(path, encoding="utf-8") as handle:
        return manifest_facts(yaml.safe_load(handle))


# ---------------------------------------------------------------------------------------------------------------
# promoteArtifacts


def artifact_path(job: str, revision: str, build_number: str, platform: str, architecture: str, distribution: str) -> str:
    return f"{job}/{revision}/{build_number}/{platform}/{architecture}/{distribution}"


def check_distribution(platform: str, distribution: str) -> None:
    if distribution not in PLATFORM_DISTRIBUTIONS.get(platform, []):
        # promoteArtifacts only loops over distributionMap[platform]; anything else is "Skip ... due to user inputs".
        raise SystemExit(f"promoteArtifacts promotes nothing for platform={platform} distribution={distribution} "
                         f"(allowed: {PLATFORM_DISTRIBUTIONS})")


def staged_files(build_manifest: Dict[str, Any], bundle_manifest: Dict[str, Any], filename: str, distribution: str) -> List[str]:
    """Files under the staging artifactPath that promoteArtifacts reads, before checksums/signatures."""
    files = [f"dist/{filename}/{posixpath.basename(bundle_manifest['build']['location'])}"]
    artifacts = build_manifest["components"][0].get("artifacts") or {}
    if distribution == "tar":
        files += [f"builds/{filename}/{path}" for path in artifacts.get("dist") or []]
        files += [f"builds/{filename}/{path}" for path in artifacts.get("core-plugins") or []]
    return files


def with_checksums_and_signatures(files: Iterable[str], filename: str, revision: str, distribution: str) -> List[str]:
    """Add the .sha512/.sig files the two fileActions (createSha512Checksums, createSignatureFiles) write.

    Core plugins are signed as a directory (tar only); the bundle and min artifacts are matched by the Groovy glob
    ``**/<f>-min-<rev>*.<dist>*,**/<f>-<rev>*.<dist>*``. sign.sh skips file types SignerPGP does not accept."""
    patterns = [f"{filename}-min-{revision}*.{distribution}*", f"{filename}-{revision}*.{distribution}*"]
    result = set(files)
    for path in files:
        base = posixpath.basename(path)
        in_core_plugins = distribution == "tar" and f"/{filename}/core-plugins/" in f"/{path}"
        if not in_core_plugins and not any(fnmatch.fnmatchcase(base, p) for p in patterns):
            continue
        if base.endswith(SHA512_TYPES):
            result.add(path + ".sha512")
        if base.endswith(PGP_TYPES):
            result.add(path + ".sig")
    return sorted(result)


def ant_prefix_match(relative: str, prefix: str) -> bool:
    """s3Upload includePathPattern '**/<prefix>*': any file whose basename starts with prefix."""
    return posixpath.basename(relative).startswith(prefix)


def upload_plan(files: Iterable[str], build_manifest: Dict[str, Any], filename: str, revision: str,
                platform: str, architecture: str, distribution: str) -> List[Dict[str, str]]:
    """The three s3Upload calls of promoteArtifacts, applied to files relative to artifactPath."""
    files = sorted(set(files))
    plan: List[Dict[str, str]] = []

    def add(working_dir: str, prefix: str, destination: str) -> None:
        for path in files:
            if path.startswith(working_dir) and ant_prefix_match(path[len(working_dir):], prefix):
                plan.append({"source": path, "destination": f"{destination}/{path[len(working_dir):]}"})

    core_plugins_dir = f"builds/{filename}/core-plugins/"
    has_core_plugins = any(path.startswith(core_plugins_dir) for path in files)
    if has_core_plugins and distribution == "tar":
        for plugin_sub_path in build_manifest["components"][0]["artifacts"]["core-plugins"]:
            plugin_name_with_ext = plugin_sub_path.split("/")[1]
            plugin_name = plugin_name_with_ext.replace(f"-{revision}.zip", "")
            add(core_plugins_dir, plugin_name, f"releases/plugins/{plugin_name}/{revision}")
    if distribution == "tar":
        add(f"builds/{filename}/dist/", f"{filename}-min-{revision}-{platform}-{architecture}",
            f"releases/core/{filename}/{revision}")
    add(f"dist/{filename}/", f"{filename}-{revision}-{platform}-{architecture}", f"releases/bundle/{filename}/{revision}")
    return plan


def local_files(root: str) -> List[str]:
    found = []
    for directory, _, names in os.walk(root):
        for name in names:
            found.append(os.path.relpath(os.path.join(directory, name), root).replace(os.sep, "/"))
    return sorted(found)


# ---------------------------------------------------------------------------------------------------------------
# promoteRepos


def repos_plan(facts: Dict[str, str], job: str, build_number: str, repo_type: str, public_url: str) -> Dict[str, Any]:
    if repo_type not in DIST_TYPES:
        raise SystemExit(f"repo type must be yum or apt, got {repo_type!r}")
    dist_type = DIST_TYPES[repo_type]
    filename, revision = facts["filename"], facts["revision"]
    prod_path = f"releases/bundle/{filename}/{facts['repo-version']}/{repo_type}"
    packages = []
    for arch in ("x64", "arm64"):
        name = f"{filename}-{revision}-linux-{arch}.{dist_type}"
        if repo_type == "yum":
            destination = f"{prod_path}/{name}"
        else:
            # aptly publish snapshot: pool/<component>/<first letter>/<source package>/<file>
            destination = f"{prod_path}/pool/main/{filename[0]}/{filename}/{name}"
        packages.append({
            "source": f"{public_url}/{job}/{revision}/{build_number}/linux/{arch}/{dist_type}/dist/{filename}/{name}",
            "destination": destination,
        })
    if repo_type == "yum":
        metadata = [f"{prod_path}/repodata/repomd.xml", f"{prod_path}/repodata/repomd.xml.asc",
                    f"{prod_path}/repodata/<sha>-primary.xml.gz (createrepo --update)"]
    else:
        metadata = [f"{prod_path}/dists/stable/{name}" for name in ("Release", "InRelease", "Release.gpg")]
        metadata += [f"{prod_path}/dists/stable/main/binary-{arch}/Packages" for arch in ("amd64", "arm64")]
    return {"repo-prod-path": prod_path, "dist-type": dist_type, "packages": packages, "metadata": metadata}


def published_repo_files(production_url: str, prod_path: str, repo_type: str) -> List[str]:
    """Package paths (relative to prod_path) listed by the published yum/apt metadata."""
    base = f"{production_url}/{prod_path}"
    if repo_type == "yum":
        status, body, _ = http(f"{base}/repodata/repomd.xml")
        if status != 200:
            return []
        match = re.search(r'href="(repodata/[^"]*primary\.xml\.gz)"', body.decode())
        if not match:
            return []
        status, primary, _ = http(f"{base}/{match.group(1)}")
        if status != 200:
            return []
        return re.findall(r'<location href="([^"]+)"', gzip.decompress(primary).decode())
    files: List[str] = []
    for arch in ("amd64", "arm64"):
        status, body, _ = http(f"{base}/dists/stable/main/binary-{arch}/Packages")
        if status == 200:
            files += re.findall(r"^Filename: (\S+)$", body.decode(), flags=re.MULTILINE)
    return files


# ---------------------------------------------------------------------------------------------------------------
# promoteContainer


def docker_plan(source_images: str, release_version: str, docker_hub: bool, ecr: bool, tag_latest: bool,
                tag_major: bool, data_prepper_registry: str = "") -> List[Dict[str, str]]:
    if not source_images.strip() or not release_version.strip():
        raise SystemExit("Make sure at lease one product is added to be promoted with proper release version.")
    major = release_version.split(".")[0]
    tags = [release_version] + ([major] if tag_major else []) + (["latest"] if tag_latest else [])
    copies = []
    for product in source_images.split(","):
        image_repo = product.strip()
        if not image_repo:
            continue
        image_product = image_repo.split(":")[0]
        source_tag = image_repo.split(":")[-1]
        source_registry = data_prepper_registry if image_product == "data-prepper" else "opensearchstaging"
        if not source_registry:
            raise SystemExit("data-prepper needs the DATA_PREPPER_STAGING_CONTAINER_REPOSITORY repository variable.")
        for enabled, destination in ((docker_hub, DOCKER_PRODUCTION), (ecr, ECR_PRODUCTION)):
            if not enabled:
                continue
            for tag in tags:
                copies.append({
                    "SOURCE_IMAGE_REGISTRY": source_registry,
                    "SOURCE_IMAGE": f"{image_product}:{source_tag}",
                    "DESTINATION_IMAGE_REGISTRY": destination,
                    "DESTINATION_IMAGE": f"{image_product}:{tag}",
                })
    return copies


def registry_digest(reference: str) -> Optional[str]:
    """Manifest digest of <registry>/<repo>:<tag> on Docker Hub or ECR Public (anonymous pull token)."""
    repository, tag = reference.rsplit(":", 1)
    if repository.startswith("public.ecr.aws/"):
        repository = repository[len("public.ecr.aws/"):]
        _, token_body, _ = http(f"https://public.ecr.aws/token/?scope=repository:{repository}:pull")
        registry = "https://public.ecr.aws"
    else:
        if "/" not in repository:
            repository = f"library/{repository}"
        _, token_body, _ = http(f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repository}:pull")
        registry = "https://registry-1.docker.io"
    token = json.loads(token_body or b"{}").get("token", "")
    accept = ",".join([
        "application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json",
    ])
    status, _, headers = http(f"{registry}/v2/{repository}/manifests/{tag}", method="HEAD",
                              headers={"Authorization": f"Bearer {token}", "Accept": accept})
    return headers.get("docker-content-digest") if status == 200 else None


# ---------------------------------------------------------------------------------------------------------------
# publishToMaven


def maven_files(build_manifest: Dict[str, Any]) -> List[str]:
    """builds/<f>/maven files that stage-maven-release.sh bundles (it deletes maven-metadata*.xml* first), plus
    the .asc signatures sign.sh --type maven --sigtype .asc adds."""
    files = []
    for component in build_manifest["components"]:
        for path in (component.get("artifacts") or {}).get("maven") or []:
            if not fnmatch.fnmatchcase(posixpath.basename(path), "maven-metadata*.xml*"):
                files.append(path)
    signed = [f"{path}.asc" for path in files if path.endswith(PGP_TYPES)]
    return sorted(set(files + signed))


def maven_coordinates(files: Iterable[str]) -> List[Dict[str, Any]]:
    coordinates: Dict[Tuple[str, str, str], List[str]] = {}
    for path in files:
        parts = path.split("/")
        if parts[0] != "maven" or len(parts) < 5:
            continue
        group, artifact, version = ".".join(parts[1:-3]), parts[-3], parts[-2]
        coordinates.setdefault((group, artifact, version), []).append(parts[-1])
    return [{"group": g, "artifact": a, "version": v, "files": sorted(names)} for (g, a, v), names in sorted(coordinates.items())]


# ---------------------------------------------------------------------------------------------------------------
# Output


def write_summary(path: Optional[str], lines: List[str]) -> None:
    text = "\n".join(lines) + "\n"
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)


def emit_outputs(path: Optional[str], values: Dict[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def mark(found: Optional[bool]) -> str:
    return "n/a" if found is None else ("published" if found else "**missing**")


def run_artifacts(args: argparse.Namespace) -> Tuple[List[str], Dict[str, Any], int]:
    check_distribution(args.platform, args.distribution)
    facts = load_facts(args.input_manifest)
    path = artifact_path(args.job, facts["revision"], args.build_number, args.platform, args.architecture, args.distribution)
    staging = f"{args.public_url}/{path}"
    if args.local_root:
        with open(os.path.join(args.local_root, "builds", facts["filename"], "manifest.yml"), encoding="utf-8") as handle:
            build_manifest = yaml.safe_load(handle)
        files = local_files(args.local_root)
        origin = f"files under {args.local_root}"
    else:
        build_manifest = fetch_yaml(f"{staging}/builds/{facts['filename']}/manifest.yml")
        bundle_manifest = fetch_yaml(f"{staging}/dist/{facts['filename']}/manifest.yml")
        files = with_checksums_and_signatures(staged_files(build_manifest, bundle_manifest, facts["filename"], args.distribution),
                                              facts["filename"], facts["revision"], args.distribution)
        origin = "build manifests (dry run: .sha512/.sig are written by the real run)"
    plan = upload_plan(files, build_manifest, facts["filename"], facts["revision"], args.platform, args.architecture, args.distribution)
    missing = 0
    lines = [f"### distribution-promote-artifacts: {args.job} {facts['revision']} build {args.build_number} "
             f"{args.platform}/{args.architecture}/{args.distribution}", "",
             f"Source: `s3://$ARTIFACT_BUCKET_NAME/{path}/` (public mirror {staging}/), file list from {origin}.",
             f"Signing email: `{facts['signing-email']}`. {len(plan)} objects to `s3://$ARTIFACT_PRODUCTION_BUCKET_NAME/`.", "",
             "| # | Staging object | Production object | Published now |", "| ---: | --- | --- | --- |"]
    published: List[Optional[bool]] = []
    for index, item in enumerate(plan, 1):
        found = exists(f"{args.production_url}/{item['destination']}") if args.verify else None
        published.append(found)
        missing += 0 if found in (None, True) else 1
        lines.append(f"| {index} | `{item['source']}` | `{item['destination']}` | {mark(found)} |")
    if args.verify:
        lines += ["", f"Compared with {args.production_url}: {len(plan) - missing}/{len(plan)} already published, {missing} missing."]
    objects = [{**item, "published": found} for item, found in zip(plan, published)]
    return lines, {"artifact-path": path, **facts, "objects": objects}, missing


def run_repos(args: argparse.Namespace) -> Tuple[List[str], Dict[str, Any], int]:
    facts = load_facts(args.input_manifest)
    plan = repos_plan(facts, args.job, args.build_number, args.repo_type, args.public_url)
    published = published_repo_files(args.production_url, plan["repo-prod-path"], args.repo_type) if args.verify else []
    missing = 0
    lines = [f"### distribution-promote-repos: {args.repo_type} {args.job} {facts['revision']} build {args.build_number}", "",
             f"Sync `s3://$ARTIFACT_PRODUCTION_BUCKET_NAME/{plan['repo-prod-path']}/`, add the packages below, "
             f"{'createrepo --update + sign repomd.xml (.asc)' if args.repo_type == 'yum' else 'aptly publish snapshot (signed with the RPM release key)'}, "
             f"sync back. Signing email: `{facts['signing-email']}`.", "",
             "| Staging package | Staged | Production object | In published metadata |", "| --- | --- | --- | --- |"]
    for item in plan["packages"]:
        staged = exists(item["source"]) if args.verify else None
        relative = item["destination"][len(plan["repo-prod-path"]) + 1:]
        found = (relative in published) if args.verify else None
        item.update({"staged": staged, "published": found})
        missing += 0 if found in (None, True) else 1
        lines.append(f"| {item['source']} | {mark(staged).replace('published', 'yes')} | `{item['destination']}` | {mark(found)} |")
    lines += ["", "Regenerated metadata: " + ", ".join(f"`{m}`" for m in plan["metadata"])]
    if args.verify:
        lines += ["", f"Published {args.repo_type} metadata under {args.production_url}/{plan['repo-prod-path']}/ lists "
                      f"{len(published)} packages; {len(plan['packages']) - missing}/{len(plan['packages'])} planned packages present."]
    return lines, {**facts, **plan}, missing


def run_docker(args: argparse.Namespace) -> Tuple[List[str], Dict[str, Any], int]:
    copies = docker_plan(args.source_images, args.release_version, args.docker_hub_promote == "true", args.ecr_promote == "true",
                         args.tag_latest == "true", args.tag_major_version == "true", args.data_prepper_registry)
    missing = differs = 0
    lines = [f"### docker-promotion: {args.source_images} -> {args.release_version}", "",
             "| # | Source | Destination | Source digest | Destination digest now |", "| ---: | --- | --- | --- | --- |"]
    for index, copy in enumerate(copies, 1):
        source = f"{copy['SOURCE_IMAGE_REGISTRY']}/{copy['SOURCE_IMAGE']}"
        destination = f"{copy['DESTINATION_IMAGE_REGISTRY']}/{copy['DESTINATION_IMAGE']}"
        source_digest = destination_digest = None
        if args.verify:
            source_digest = registry_digest(source)
            destination_digest = registry_digest(destination)
            missing += 0 if destination_digest else 1
        copy.update({"source-digest": source_digest or "", "destination-digest": destination_digest or ""})
        same = ""
        if source_digest and destination_digest:
            same = " (same)" if source_digest == destination_digest else " (**differs**)"
            differs += 0 if source_digest == destination_digest else 1
        lines.append(f"| {index} | `{source}` | `{destination}` | `{(source_digest or '-')[:19]}` | "
                     f"`{(destination_digest or ('-' if not args.verify else 'missing'))[:19]}`{same} |")
    if args.verify:
        lines += ["", f"{len(copies) - missing}/{len(copies)} destination tags resolve today; "
                  f"{differs} point to a different image than the source."]
    return lines, {"copies": copies}, missing


def run_maven(args: argparse.Namespace) -> Tuple[List[str], Dict[str, Any], int]:
    builds = f"distribution-build-opensearch/{args.version}/{args.build_id}/linux/x64/tar/builds"
    build_manifest = fetch_yaml(f"{args.public_url}/{builds}/opensearch/manifest.yml")
    files = maven_files(build_manifest)
    coordinates = maven_coordinates(files)
    major = args.version.split(".")[0]
    missing = 0
    lines = [f"### publish-to-maven: OpenSearch {args.version} build {args.build_id}", "",
             f"Download `s3://$ARTIFACT_BUCKET_NAME_LF/{builds}/`, sign `opensearch/manifest.yml` (type maven, .asc, "
             f"`{'release@opensearch.org' if int(major) > 2 else 'opensearch@amazon.com'}`), "
             f"`stage-maven-release.sh -d opensearch/maven -a true`: {len(files)} files, {len(coordinates)} coordinates.", "",
             "| # | Coordinate | Files (incl. .asc) | On Maven Central now |", "| ---: | --- | ---: | --- |"]
    for index, coordinate in enumerate(coordinates, 1):
        found = None
        if args.verify:
            group_path = coordinate["group"].replace(".", "/")
            pom = f"{coordinate['artifact']}-{coordinate['version']}.pom"
            found = exists(f"{MAVEN_CENTRAL_URL}/{group_path}/{coordinate['artifact']}/{coordinate['version']}/{pom}")
            missing += 0 if found else 1
            time.sleep(args.delay)
        coordinate["published"] = found
        lines.append(f"| {index} | `{coordinate['group']}:{coordinate['artifact']}:{coordinate['version']}` | "
                     f"{len(coordinate['files'])} | {mark(found)} |")
    if args.verify:
        lines += ["", f"Compared with {MAVEN_CENTRAL_URL}: {len(coordinates) - missing}/{len(coordinates)} coordinates published."]
    return lines, {"files": files, "coordinates": coordinates}, missing


def main(argv: Optional[List[str]] = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--public-url", default=os.environ.get("PUBLIC_ARTIFACT_URL") or DEFAULT_PUBLIC_ARTIFACT_URL)
    common.add_argument("--production-url", default=DEFAULT_PRODUCTION_URL)
    common.add_argument("--verify", action="store_true", help="compare the plan with what is published today")
    common.add_argument("--strict", action="store_true", help="with --verify, exit 1 when a destination is missing")
    common.add_argument("--summary", help="append the markdown table here (e.g. $GITHUB_STEP_SUMMARY); default stdout")
    common.add_argument("--json", help="write the plan as JSON here")
    common.add_argument("--github-output", help="append key=value outputs here (e.g. $GITHUB_OUTPUT)")
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    artifacts = commands.add_parser("artifacts", parents=[common])
    artifacts.add_argument("--job", required=True)
    artifacts.add_argument("--build-number", required=True)
    artifacts.add_argument("--input-manifest", required=True)
    artifacts.add_argument("--platform", required=True)
    artifacts.add_argument("--architecture", required=True)
    artifacts.add_argument("--distribution", required=True)
    artifacts.add_argument("--local-root", help="downloaded+signed copy of the staging artifactPath (real run)")

    repos = commands.add_parser("repos", parents=[common])
    repos.add_argument("--job", required=True)
    repos.add_argument("--build-number", required=True)
    repos.add_argument("--input-manifest", required=True)
    repos.add_argument("--repo-type", required=True)

    docker = commands.add_parser("docker", parents=[common])
    docker.add_argument("--source-images", required=True)
    docker.add_argument("--release-version", required=True)
    for flag in ("--docker-hub-promote", "--ecr-promote", "--tag-latest", "--tag-major-version"):
        docker.add_argument(flag, choices=["true", "false"], default="true")
    docker.add_argument("--data-prepper-registry", default="")

    maven = commands.add_parser("maven", parents=[common])
    maven.add_argument("--build-id", required=True)
    maven.add_argument("--version", required=True)
    maven.add_argument("--delay", type=float, default=0.0, help="seconds between Maven Central requests")

    args = parser.parse_args(argv)
    runner = {"artifacts": run_artifacts, "repos": run_repos, "docker": run_docker, "maven": run_maven}[args.command]
    lines, plan, missing = runner(args)
    write_summary(args.summary, lines)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, indent=2)
    outputs = {"missing": str(missing)}
    if args.command == "docker":
        outputs["copies"] = json.dumps([{k: c[k] for k in c if k.isupper()} for c in plan["copies"]], separators=(",", ":"))
        outputs["images"] = json.dumps(sorted({c["SOURCE_IMAGE"] for c in plan["copies"]}), separators=(",", ":"))
    if args.command in ("artifacts", "repos"):
        outputs.update({k: plan[k] for k in ("filename", "revision", "repo-version", "signing-email")})
    if args.command == "artifacts":
        outputs["artifact-path"] = plan["artifact-path"]
    emit_outputs(args.github_output, outputs)
    return 1 if args.strict and args.verify and missing else 0


if __name__ == "__main__":
    sys.exit(main())
