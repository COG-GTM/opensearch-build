# Jenkins to GitHub Actions: release promotion

This document describes the additive migration of five release/promotion jobs in
the `COG-GTM/opensearch-build` fork. The Jenkins files remain in place for
side-by-side review. None of these workflows was dispatched: they need the
OpenSearch build infrastructure, AWS accounts, signing credentials, Docker
registries, and publishing credentials.

## 1. Files added

| GitHub Actions file | Jenkins source |
| --- | --- |
| `.github/workflows/distribution-promote-artifacts.yml` | `jenkins/release-workflows/promote-artifacts.jenkinsfile:19-82` |
| `.github/workflows/distribution-promote-repos.yml` | `jenkins/release-workflows/promote-repos.jenkinsfile:23-79` |
| `.github/workflows/docker-promotion.yml` | `jenkins/release-workflows/promote-docker-ecr-lf.jenkinsfile:18-101` |
| `.github/workflows/publish-to-maven.yml` | `jenkins/release-workflows/publish-to-maven-lf.jenkinsfile:23-87` |
| `.github/workflows/distribution-release-tag-creation.yml` | `jenkins/release-workflows/release-tag.jenkinsfile:18-85` |
| `.github/actions/promote-artifacts/action.yml` | `opensearch-build-libraries:vars/promoteArtifacts.groovy:21-130` |
| `.github/actions/promote-repos/action.yml` | `opensearch-build-libraries:vars/promoteRepos.groovy:19-243` |
| `.github/actions/promote-container/action.yml` | `opensearch-build-libraries:vars/promoteContainer.groovy:20-110` |
| `.github/actions/copy-container/action.yml` | `opensearch-build-libraries:vars/copyContainer.groovy:19-89` |
| `.github/actions/publish-to-maven/action.yml` | `opensearch-build-libraries:vars/publishToMaven.groovy:17-41` |
| `.github/actions/create-release-tag/action.yml` | `opensearch-build-libraries:vars/createReleaseTag.groovy:9-70` |
| `.github/actions/create-sha512-checksums/action.yml` | `opensearch-build-libraries:vars/createSha512Checksums.groovy:9-63` |
| `.github/actions/sign-artifacts-client/action.yml` | `opensearch-build-libraries:vars/signArtifacts.groovy:123-255` |
| `.github/actions/download-from-s3/action.yml` | `opensearch-build-libraries:vars/downloadFromS3.groovy`; copied byte-for-byte from PR #1 |
| `.github/scripts/release_manifest_facts.py` | `opensearch-build-libraries:src/jenkins/InputManifest.groovy:74-147`, `BundleManifest.groovy:14-109` |

All five workflows are dispatch-only (`workflow_dispatch`) and all five also
expose `workflow_call` so the out-of-scope release pipeline can chain them
later. No workflow has a `push` or `pull_request` trigger.

## 2. Construct mapping

| Jenkins construct | GitHub Actions equivalent |
| --- | --- |
| `parameters {}` (`promote-artifacts.jenkinsfile:23-56`; `promote-repos.jenkinsfile:28-51`; `promote-docker-ecr-lf.jenkinsfile:30-61`; `publish-to-maven-lf.jenkinsfile:32-42`; `release-tag.jenkinsfile:30-35`) | `workflow_dispatch.inputs` plus matching `workflow_call.inputs`. Jenkins choices become dispatch choices; reusable workflows receive strings because `workflow_call` has no choice type. |
| `agent { docker { ... } }` (`promote-artifacts.jenkinsfile:59-65`; `promote-repos.jenkinsfile:54-60`; `promote-docker-ecr-lf.jenkinsfile:22-28`; `publish-to-maven-lf.jenkinsfile:24-30`; `release-tag.jenkinsfile:23-27`) | `runs-on: ubuntu-24.04` with a job `container`. The repository job container is selected in `resolve-image` for yum versus apt. Hosted runners are smaller than the Jenkins `m5.4xlarge` Docker hosts. Docker promotion omits the Jenkins Docker socket mount because a job container cannot reproduce that host mount; `crane` does not need the daemon. |
| `options { timeout(...) }` (`promote-artifacts.jenkinsfile:19-21`; `promote-repos.jenkinsfile:24-26`; `promote-docker-ecr-lf.jenkinsfile:19-21`; `release-tag.jenkinsfile:19-21`) | Job `timeout-minutes`: 60 minutes for artifact, repository and Docker promotion, 120 minutes for release tags. `publish-to-maven-lf.jenkinsfile` has **no** `options { timeout }`; its 60-minute cap is an addition, not a port. |
| `post { always { cleanWs() } }` (`promote-artifacts.jenkinsfile:75-81`; `release-tag.jenkinsfile:79-84`) | No cleanup step: Actions jobs use ephemeral workspaces. `postCleanup()` and `cleanWs()` are intentionally not recreated. |
| `post { always { postCleanup() } }` (`promote-repos.jenkinsfile:72-77`; `promote-docker-ecr-lf.jenkinsfile:94-100`; `publish-to-maven-lf.jenkinsfile:80-85`) | No equivalent is needed on an ephemeral runner. Docker logout remains in `copy-container` because it is part of the Jenkins copy behavior. |
| `currentBuild.description = ...` (`promote-artifacts.jenkinsfile:69`; `promote-repos.jenkinsfile:64`; `promote-docker-ecr-lf.jenkinsfile:67`) | `$GITHUB_STEP_SUMMARY` in the parameter-check job or the corresponding action. |
| `currentBuild.result = 'ABORTED'` plus `error()` (`promote-docker-ecr-lf.jenkinsfile:68-70`) | A failed validation step. Actions has no separate ABORTED result; the error is explicit and stops the job. |
| The serial `for (product in SOURCE_IMAGES.split(','))` loop (`promote-docker-ecr-lf.jenkinsfile:78-89`) | A matrix generated from the comma-separated input. This intentionally changes fan-out from serial Jenkins execution to parallel Actions jobs; the document calls out the operational difference. |
| `build job:` (`release-tag.jenkinsfile:68-77`; `promoteContainer.groovy:39-107`) | The manifest-lock job accepts an optional workflow name and otherwise emits a notice with Jenkins' exact parameters. Docker copy is implemented locally in this slice because the `docker-copy` migration is owned by another session. |
| `withAWS(role:, roleAccount:)` (`promoteArtifacts.groovy:55-57,91-125`; `promoteRepos.groovy:69-73,238-241`; `copyContainer.groovy:59-64`) | Job `permissions: id-token: write` and pinned `aws-actions/configure-aws-credentials` with `role-to-assume: arn:aws:iam::${{ inputs.aws-account }}:role/${{ inputs.role-name }}` and `role-duration-seconds: 900`. |
| `withSecrets` / 1Password (`promoteArtifacts.groovy:13-19,43`; `promoteRepos.groovy:63-67,169-180`; `copyContainer.groovy:20-33,44-64`; `publishToMaven.groovy:34-40`) | Repository or environment secrets are exported by the caller job and passed into composite-action inputs. Composite actions cannot read the `secrets` context. |
| `library(identifier: 'jenkins@12.0.0')` (`promote-artifacts.jenkinsfile:13-16`; `promote-repos.jenkinsfile:13-16`; `release-tag.jenkinsfile:13-16`) | Source behavior was read from the cloned shared library. The action code is local and reviewable. |
| `library(identifier: 'jenkins@lf-jenkins')` (`promote-docker-ecr-lf.jenkinsfile:13-16`; `publish-to-maven-lf.jenkinsfile:13-16`) | Docker behavior is local for this slice; the Maven resource `resources/publish/stage-maven-release.sh` is fetched from the `lf-jenkins` ref at runtime rather than copied into this repository. |
| `readYaml` / `findFiles` / `s3Download` / `s3Upload` / `cleanWs` (`promoteArtifacts.groovy:24-25,59-60,84-87,101-124`; `promoteRepos.groovy:22-23,72,240`; `promote-artifacts.jenkinsfile:78`) | `release_manifest_facts.py`, `find`, `aws s3 cp/sync` with `--exclude '*' --include`, and ephemeral job workspaces. The glob dialects differ: the Jenkins Ant pattern `**/x*` means "any depth, including none", while an aws-cli `--include "**/x*"` requires a literal `/`; the faithful aws-cli translation is `*x*`, because aws-cli `*` already crosses `/`. |

## 3. Shared-library steps converted

* `promoteArtifacts` became `.github/actions/promote-artifacts`. It preserves
  the Linux/windows distribution map and prints `Skip <distribution> due to
  user inputs`. The offered `darwin` choice has no map entry in Jenkins; the
  Actions port fails clearly rather than iterating a null value.
* `promoteRepos` became `.github/actions/promote-repos`, including the yum
  `repomd.xml` to `repomd.pom` rename and the apt `aptly` flow.
* `promoteContainer` became `.github/actions/promote-container`.
* `copyContainer` became `.github/actions/copy-container`; its `allTags`
  guard remains even though this slice never sets it.
* `publishToMaven` became `.github/actions/publish-to-maven`. It signs the
  manifest, fetches the library resource, and runs `-a true`.
* `createReleaseTag` became `.github/actions/create-release-tag`. Existing
  tags at the same commit are skipped, conflicting tags fail, and push
  failures are collected and reported at the end. The bot token is passed
  through `http.extraheader` rather than embedded in a remote URL.
* `createSha512Checksums` became `.github/actions/create-sha512-checksums`.
  The Linux path is ported; the Windows `bat` branch is unreachable from the
  Linux container jobs and is intentionally not included.
* `createSignatureFiles` is a closure wrapper around `signArtifacts`
  (`opensearch-build-libraries:vars/createSignatureFiles.groovy:13-15`).
  The artifact action calls the client signer with `.sig`.
* The non-rpm/client branch of `signArtifacts` became
  `.github/actions/sign-artifacts-client`; it imports both public keys and
  invokes the repository-root `sign.sh`.
* `downloadFromS3` is copied byte-for-byte from PR #1.
* `loadCustomScript` became a fetch of a library resource at the
  `opensearch-build-libraries/lf-jenkins` ref. This is a trust decision: the
  remote resource must be pinned to an immutable reviewed commit before
  production use.
* `postCleanup` has no equivalent because the runner is ephemeral.

There is no `vars/withSecrets.groovy` in the shared library at `main`.
`withSecrets` is supplied by the LF Jenkins 1Password integration, so the
exact binding, masking, and failure semantics are an assumption here. The
Actions design requires callers to configure the listed GitHub secrets and
fails explicitly for signer-client values that are empty.

## 4. Overlap with PR #1

`download-from-s3/action.yml` is byte-for-byte identical to PR #1's file.
At merge time, retain one copy so the branches deduplicate cleanly.

PR #1's `sign-artifacts/action.yml` supports only `sigtype: .rpm` and fails
for other suffixes. This slice adds `sign-artifacts-client/action.yml` for the
PGP/client branch (`.sig` for artifact promotion and `.asc` for yum metadata
and Maven). At merge time, fold the two implementations into one
`sign-artifacts` action that dispatches on `sigtype`; do not silently replace
PR #1's rpm branch.

## 5. Required secrets and variables

Values are names only here; no secret values are stored in this repository.
The Jenkins references are the references observed in the shared-library
source.

| Name | Used by | Jenkins 1Password reference |
| --- | --- | --- |
| `AWS_ACCOUNT_PUBLIC` | S3 staging downloads | `op://opensearch-release-secrets/aws-accounts/jenkins-aws-account-public` |
| `ARTIFACT_BUCKET_NAME` | S3 staging bucket | `op://opensearch-release-secrets/aws-resource-arns/jenkins-artifact-bucket-name` |
| `ARTIFACT_PROMOTION_ROLE_NAME` | Production S3/ECR role | `op://opensearch-release-secrets/aws-iam-roles/jenkins-artifact-promotion-role` |
| `AWS_ACCOUNT_ARTIFACT` | Production S3/ECR account | `op://opensearch-release-secrets/aws-accounts/jenkins-aws-production-account` |
| `ARTIFACT_PRODUCTION_BUCKET_NAME` | Public production bucket | `op://opensearch-release-secrets/aws-resource-arns/jenkins-artifact-production-bucket-name` |
| `RPM_SIGNING_ACCOUNT` | apt RPM-signing assume role | `op://opensearch-release-secrets/rpm-signing/jenkins-rpm-signing-account-number` |
| `RPM_RELEASE_SIGNING_PASSPHRASE_SECRETS_ARN` | apt release-key passphrase | `op://opensearch-release-secrets/rpm-signing/jenkins-rpm-release-signing-passphrase-secrets-arn` |
| `RPM_RELEASE_SIGNING_SECRET_KEY_ID_SECRETS_ARN` | apt release secret key | `op://opensearch-release-secrets/rpm-signing/jenkins-rpm-release-signing-secret-key-secrets-arn` |
| `RPM_RELEASE_SIGNING_KEY_ID` | apt release key selection | `op://opensearch-release-secrets/rpm-signing/jenkins-rpm-release-signing-key-id` |
| `RPM_SIGNING_PASSPHRASE_SECRETS_ARN` | apt legacy-key passphrase | `op://opensearch-release-secrets/rpm-signing/jenkins-rpm-signing-passphrase-secrets-arn` |
| `RPM_SIGNING_SECRET_KEY_ID_SECRETS_ARN` | apt legacy secret key | `op://opensearch-release-secrets/rpm-signing/jenkins-rpm-signing-secret-key-secrets-arn` |
| `RPM_SIGNING_KEY_ID` | apt legacy key selection | `op://opensearch-release-secrets/rpm-signing/jenkins-rpm-signing-key-id` |
| `SIGNER_CLIENT_ROLE` | `.sig`/`.asc` signer client | `op://opensearch-release-secrets/client-signing/jenkins-signer-client-role` |
| `SIGNER_CLIENT_EXTERNAL_ID` | signer client | `op://opensearch-release-secrets/client-signing/jenkins-signer-client-external-id` |
| `SIGNER_CLIENT_UNSIGNED_BUCKET` | signer client | `op://opensearch-release-secrets/client-signing/jenkins-signer-client-unsigned-bucket` |
| `SIGNER_CLIENT_SIGNED_BUCKET` | signer client | `op://opensearch-release-secrets/client-signing/jenkins-signer-client-signed-bucket` |
| `GITHUB_BOT_USER` | signer client GitHub access | `op://opensearch-release-secrets/github-bot/ci-bot-username` |
| `GITHUB_BOT_TOKEN` | signer client GitHub access, component tag pushes, optional workflow dispatch | `op://opensearch-release-secrets/github-bot/ci-bot-token` |
| `DOCKERHUB_READONLY_USERNAME` / `DOCKERHUB_READONLY_PASSWORD` | read-only Docker Hub login for staging pulls | `op://opensearch-release-secrets/dockerhub-production-readonly-credentials/{username,password}` |
| `DOCKERHUB_PRODUCTION_USERNAME` / `DOCKERHUB_PRODUCTION_PASSWORD` | production Docker Hub login | `op://opensearch-release-secrets/dockerhub-production-credentials/{username,password}` |
| `SONATYPE_USERNAME` / `SONATYPE_PASSWORD` | Maven Central publication | `op://opensearch-release-secrets/maven-central-portal-credentials/{username,password}` |
| `SONATYPE_STAGING_PROFILE_ID` | Maven staging profile | `opensearch-release-secrets` Jenkins environment binding; configure the profile ID as a GitHub secret |

GitHub rejects repository and environment secret names that start with
`GITHUB_`, and a job-level `env: GITHUB_TOKEN:` would shadow the automatic
token. The Jenkins 1Password bindings named `GITHUB_USER`/`GITHUB_TOKEN`
(`createReleaseTag.groovy:10-13`, `signArtifacts.groovy`) are therefore
configured as `GITHUB_BOT_USER`/`GITHUB_BOT_TOKEN` and exported into the
composite actions under the Jenkins names inside the signing step only.

`PUBLIC_ARTIFACT_URL` is a repository variable, not a secret, and replaces the
Jenkins `PUBLIC_ARTIFACT_URL` environment value in `promoteRepos.groovy:56-57`.
`DATA_PREPPER_STAGING_CONTAINER_REPOSITORY` is a repository variable that must
be configured before any `data-prepper:*` image is promoted. Jenkins references
the variable directly (`promoteContainer.groovy:32`) and would have failed on an
unset value. The action fails explicitly instead of falling back to
`opensearchstaging`: a silent fallback would publish a production image pulled
from the wrong source registry.

OIDC setup follows the PR #1 model:

1. Add `token.actions.githubusercontent.com` as an AWS OIDC provider.
2. Create a role per AWS account and allow the repository subject
   `repo:COG-GTM/opensearch-build:*` (tighten it to the protected branch or
   environment before production use).
3. Grant each role only the Jenkins-equivalent permissions: `opensearch-bundle`
   for staging reads, the artifact-promotion role for production S3/ECR, and
   `jenkins-prod-rpm-signing-assume-role` for apt signing.
4. Configure GitHub Environment reviewers for the production environments below.

## 6. Manual approval gates and irreversible effects

| Workflow | Environment | What a wrong input can publish |
| --- | --- | --- |
| `distribution-promote-artifacts.yml` | `production-artifacts` | A wrong build number or input manifest can irreversibly publish the wrong tar/rpm/deb/zip, plugin, or min artifact into the public production release bucket. This is the most dangerous mis-migration target in this slice. |
| `distribution-promote-repos.yml` | `production-repositories` | Wrong version, build, or repo type can replace yum metadata/signatures or publish an incorrect apt repository. |
| `docker-promotion.yml` | `production-containers` | Wrong source image or release version can publish to Docker Hub/public ECR. `TAG_LATEST` and `TAG_MAJOR_VERSION` default to `true`, so a mis-dispatched run can move the public `latest` and major tags. An unset `DATA_PREPPER_STAGING_CONTAINER_REPOSITORY` would, with a silent fallback, publish a production `data-prepper` image copied from the wrong source registry; the action fails instead. |
| `publish-to-maven.yml` | `production-maven` | `autoPublish: true` publishes to Maven Central and is effectively irreversible. |
| `distribution-release-tag-creation.yml` | `production-release-tags` | Wrong release version or manifest can push tags across every component repository. |

Jenkins had no `input` step for artifact promotion or repository promotion.
The first two environments are deliberately added controls because those jobs
write production release data. The Docker and Maven jobs likewise publish
outside the repository, and the tag job pushes to many component repositories.

## 7. Not migrated / needs a decision

* Jenkins `lock` resources are not used by these five jobs. `concurrency:` is
  not a faithful general replacement: Actions keeps one pending run and can
  cancel a pending run, while Jenkins lock queues are FIFO. Add a deliberately
  chosen group only after deciding the desired queue semantics.
* `buildDiscarder` is not present in these source files. Configure retention at
  repository/organization level rather than pretending it is a workflow step.
* Jenkins manual `input` approvals are not present in this slice. GitHub
  Environment reviewers provide the added production gates.
* `release-manifest-commit-lock` remains out of scope. The tag workflow emits
  a notice with `RELEASE_VERSION` and `MANIFEST_LOCK_ACTION=UPDATE_TO_TAGS` when
  no workflow is supplied, or dispatches and waits for the explicitly supplied
  workflow.
* `docker-copy` is out of scope and owned by another migration. This branch
  duplicates its needed copy behavior locally; once that workflow lands,
  `promote-container` should probably call its reusable workflow instead.
* Warm Jenkins workspaces, `cleanWs`, and agent-specific capacity do not map to
  hosted runners. The Docker socket mount from
  `promote-docker-ecr-lf.jenkinsfile:27` is not reproduced; `crane` is used
  because it copies registry manifests without a daemon.
* Windows, macOS, and `jar_signer` branches of `signArtifacts` are not reached
  by these Linux promotion paths. The `darwin` choice is retained for fidelity,
  but fails explicitly because Jenkins' `distributionMap` has no darwin key.
* `loadCustomScript` fetches a mutable library branch (`lf-jenkins`) for Maven.
  Pinning that resource to a reviewed immutable commit is required before
  production enablement.
* Hosted runner sizing is unverified against real release artifacts; Jenkins
  used m5.4xlarge Docker hosts.

## 8. Known smells kept on purpose

* The artifact workflow retains the Jenkins distribution map, including the
  `darwin` choice that has no map entry, but turns the null iteration into a
  clear failure.
* Artifact promotion still uses the caller-supplied build number and manifest
  to write directly to the production bucket. It does not add a safety lookup
  or make the operation reversible.
* The apt branch continues to use the **rpm** signing 1Password values from
  `promoteRepos.groovy:169-180`, including the two legacy/release key paths.
* The yum branch renames `repomd.xml` to `repomd.pom` before signing and restores
  it afterward. The comment not to add `.xml` to the signer filter is retained.
* Docker promotion fans out matrix entries in parallel rather than preserving
  Jenkins' serial `for` loop. This is intentional and must be considered when
  reviewing registry rate limits and partial failures.
* `TAG_LATEST` and `TAG_MAJOR_VERSION` retain Jenkins' `true` defaults.
* The local Docker copy implementation intentionally duplicates the
  out-of-scope `docker-copy` job until the two migrations can be folded
  together.

## 9. Validation performed

Validation is static. No workflow was dispatched and no production AWS,
signing, Docker, or Maven operation was attempted.

| Check | Result |
| --- | --- |
| `actionlint` (v1.7.12) over the five new workflows | clean, zero findings |
| `yamllint` with the repository `.yamllint.yml` over every new workflow and composite action | exit 0; one `too few spaces before comment` warning remains in `download-from-s3/action.yml`, which is copied byte-for-byte from PR #1 and deliberately unmodified |
| Python YAML parse and trailing-newline check over every new file | passed |
| `flake8`, `isort --check`, `mypy` for `.github/scripts/release_manifest_facts.py` | passed |
| `release_manifest_facts.py input-manifest-facts --manifest manifests/3.9.0/opensearch-3.9.0.yml` | `filename=opensearch`, `version=3.9.0`, `qualifier=`, `revision=3.9.0`, `major-version=3`, `signing-email=release@opensearch.org`, `repo-version=3.x` |
| `release_manifest_facts.py bundle-manifest-components` against the released `opensearch-2.19.0-linux-x64.tar.gz` manifest | 27 components; `OpenSearch` tagged `2.19.0`, every other component tagged `2.19.0.0` |
| Same subcommand against the released `opensearch-3.0.0-alpha1` manifest (qualifier case) | 27 components; `OpenSearch` tagged `3.0.0-alpha1`, every other component tagged `3.0.0.0-alpha1`, matching `createReleaseTag.groovy:33-42` |
| `pre-commit run --all-files` (isort, flake8, mypy, pytest, yamllint) | all hooks passed |
| Merge-base diff audit | no file under `jenkins/` or `tests/jenkins/` is touched |
| Trigger audit | no new workflow declares `push` or `pull_request` |

`release_manifest_facts.py build-manifest-core-plugins` is exercised only by
unit-level reasoning against `promoteArtifacts.groovy:95`: no build manifest
containing `components[0].artifacts.core-plugins` is published as a downloadable
artifact, so that accessor is **unverified against real data**.

Live cloud behavior, approval configuration, signer-client credentials,
runner capacity, the remote Maven resource, and component-repository push
permissions remain unverified until an authorized operator configures and
dispatches a controlled run.
