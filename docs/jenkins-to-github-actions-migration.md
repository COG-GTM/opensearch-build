# Jenkins to GitHub Actions Migration Plan

This document is a plan, not a decision. It inventories the CI that exists in this repository today, maps each Jenkins job category onto a GitHub Actions equivalent, and lists the decisions and risks that need owners before any pipeline is moved.

## 1. Overview

### Current state: two CI systems

`opensearch-build` runs on two CI systems at once.

**Jenkins** (`jenkins/**/*.jenkinsfile`, 56 pipelines) runs everything that needs long timeouts, large or non-Linux hardware, credentials, or artifact publishing:

| Area | Files | Notes |
| --- | --- | --- |
| Distribution builds | `jenkins/opensearch/distribution-build.jenkinsfile`, `jenkins/opensearch-dashboards/distribution-build.jenkinsfile`, `jenkins/opensearch/feature-build.jenkinsfile`, `jenkins/opensearch/publish-min-snapshots.jenkinsfile` | 4-hour timeout, ~7 parallel platform/distribution stages each |
| Testing | `integ-test`, `bwc-test`, `smoke-test-lf` (OpenSearch and Dashboards), `integ-test-notification.jenkinsfile` | integ-test has a 12-hour timeout |
| Benchmarking | `jenkins/opensearch/benchmark-test.jenkinsfile`, `benchmark-test-endpoint`, `benchmark-test-vectorsearch`, `benchmark-test-datafusion`, `benchmark-pull-request`, `benchmark-compare` | webhook-triggered and cron-triggered |
| gradle-check | `jenkins/legacy/gradle/gradle-check.jenkinsfile`, `jenkins/gradle/gradle-check-flaky-test-issue-creation.jenkinsfile` | webhook-triggered from OpenSearch core PRs, `maxConcurrentTotal: 45` |
| Orchestration | `jenkins/check-for-build.jenkinsfile` | cron-driven, SHA-gated trigger of the distribution builds |
| Release workflows | `jenkins/release-workflows/*.jenkinsfile` (12 files) | promotion, maven publish, release branch/tag, release notes, chores, schedule |
| Docker | `jenkins/docker/docker-{build,copy,scan,re-release}-lf.jenkinsfile` | multi-arch image build/promotion on dedicated docker-builder agents |
| Packer | `jenkins/packer/packer-build-lf.jenkinsfile` | builds the Jenkins agent AMIs themselves |
| Validation | `jenkins/validate-artifacts/validate-artifacts-lf.jenkinsfile` | dynamically generated parallel stages per distribution × architecture |
| Legacy | `jenkins/legacy/**` | perf-test, rpm-validation, whitesource scan, maven sign, `build.ci.backups/**` |

**GitHub Actions** (`.github/workflows/*.yml`, 26 workflows) runs PR-time checks and repository automation: `python-tests.yml`, `groovy-tests.yml`, `yaml-lint.yml`, `dockerfile-lint.yml`, `license-header-checker.yml`, `link-checker.yml`, `manifests.yml`, plus issue/PR automation (`backport-pr.yml`, `add-untriaged.yml`, `automatic-merges.yml`, `github-merit-badger.yml`, `issue-dedupe*.yml`), release automation (`releases.yml`, `create-release-issues.yml`, `os-release-issues.yml`, `osd-release-issues.yml`, `os-increment-plugin-versions.yml`, `osd-increment-plugin-versions.yml`, `publish-release.yml`), and `publish-wiki.yml`.

Three existing workflows are the precedents this plan builds on:

- `get-ci-image-tag.yml` — a `workflow_call` reusable workflow that resolves the CI-runner image tag and container start options, and returns them as outputs. This is the model for every "shared library function" that becomes a reusable workflow.
- `python-tests.yml` — consumes `get-ci-image-tag.yml` and runs jobs inside `container: ${{ needs.Get-CI-Image-Tag.outputs.ci-image-version-linux }}` with `options: ...ci-image-start-options`, across a `matrix` that includes `ubuntu-24.04`, `ubuntu-24.04-arm`, `macos-15`, `macos-15-intel`, and `windows-2022`. This is the model for containerized build/test jobs on the same ci-runner images Jenkins uses.
- `manifests.yml` — computes a JSON matrix in one job (`outputs.matrix`) and consumes it in the next via `matrix: ${{ fromJson(needs.list-changed-manifests.outputs.matrix) }}`. This is the model for the dynamically generated parallel stages in `validate-artifacts-lf.jenkinsfile` and for per-component fan-out.

### Goals

1. One CI system, one place to read logs, one permission model.
2. Pipeline definitions and the code they build live in the same repository and change in the same pull request.
3. Remove the dependency on a self-managed Jenkins controller and on the external `opensearch-build-libraries` shared library.
4. No regression in build matrix coverage (linux x64/arm64, windows x64; tar/rpm/deb/zip; docker), artifact layout in S3, or release provenance.

Non-goals for the first pass: changing what is built, the manifest schema, the S3 artifact layout, or the ci-runner images.

## 2. Job inventory and migration mapping

### Jenkins concept to GitHub Actions concept

| Jenkins construct | Where it appears | GitHub Actions equivalent |
| --- | --- | --- |
| `parameterizedCron` with per-line parameter sets | `check-for-build`, `gradle-check`, `benchmark-*`, `publish-min-snapshots`, `release-schedule`, `docker-re-release` | `on.schedule` plus `on.workflow_dispatch`; one `schedule` entry per cron with the parameter set encoded in a matrix or in a checked-in config file, since `schedule` cannot carry inputs |
| `GenericTrigger` webhook (JSON post-content parameters) | `gradle-check`, `benchmark-pull-request` | `on.repository_dispatch` (`client_payload`) or `on.workflow_dispatch` invoked by the upstream repo, or `pull_request_target` when the trigger is a PR in the same org |
| `parameters { string/choice/booleanParam }` | every pipeline | `on.workflow_dispatch.inputs` and `on.workflow_call.inputs` (typed: `string`, `choice`, `boolean`) |
| `build job: 'x', parameters: [...], wait: true` | `check-for-build`, `distribution-build` triggering integ/smoke/bwc/validation | `jobs.<id>.uses: ./.github/workflows/x.yml` with `with:` (reusable workflow, same-run) or `gh workflow run` / `actions/github-script` dispatch for cross-repo |
| `stages { }` / nested `parallel { }` | `distribution-build`, `release-promotion`, `validate-artifacts` | `jobs:` with `needs:` for ordering; `strategy.matrix` for the platform/distribution fan-out |
| `agent { docker { image ... args ... } }` | almost all pipelines | `container: { image:, options: }` on the job, image resolved by the `get-ci-image-tag.yml` pattern |
| `agent { label 'Jenkins-Agent-...' }` | distribution builds, benchmark, validation | `runs-on:` with self-hosted runner labels (see decision 4a) |
| `throttleJobProperty(maxConcurrentTotal: N)` | `gradle-check` (45), `benchmark-pull-request` (20), `benchmark-compare` (20) | `concurrency` groups for serialization; note that GHA has no built-in "N concurrent" throttle — see risks |
| `lock(resource: ..., skipIfLocked: true)` | `check-for-build` | `concurrency: { group: ..., cancel-in-progress: false }`, which queues rather than skips — behaviour differs, see risks |
| `withSecrets(secrets: [[envVar:, secretRef: 'op://...']])` | 33 call sites | `secrets:` in the workflow, or the 1Password `load-secrets-action`, or AWS OIDC via `aws-actions/configure-aws-credentials` |
| `library(identifier: 'jenkins@12.0.0', ...)` | every pipeline | composite actions in `.github/actions/**`, reusable workflows in `.github/workflows/**`, or plain scripts in `scripts/` — see decision 4c |
| `archiveArtifacts` / `junit` | gradle-check, tests | `actions/upload-artifact`, plus a test-report action for JUnit XML rendering |
| `currentBuild.description = '...'` | gradle-check, several release jobs | `$GITHUB_STEP_SUMMARY` markdown |
| `postCleanup()` (148 call sites) | every pipeline | ephemeral runners make most of it unnecessary; the remainder becomes an `if: always()` cleanup step or a composite action |
| `timeout(time: 12, unit: 'HOURS')` | integ-test | `timeout-minutes:` on the job (GitHub-hosted jobs cap at 6h; self-hosted jobs do not) |
| `buildDiscarder(logRotator(...))` | check-for-build, integ-test | artifact/log `retention-days` |

### Category-level mapping

| Jenkins category | Target GitHub Actions shape | Complexity |
| --- | --- | --- |
| Distribution builds (2 pipelines) | one reusable workflow per product, matrix over platform × arch × distribution, self-hosted runners | High |
| Testing: integ-test, bwc-test, smoke-test (5 pipelines) | reusable workflows called by the distribution workflow and dispatchable standalone | High |
| Benchmark (6 pipelines) | `workflow_dispatch` + `schedule` + `repository_dispatch` from core PR comments | Medium |
| gradle-check (2 pipelines) | `repository_dispatch` from `opensearch-project/OpenSearch`, self-hosted large runner | High |
| Orchestration: check-for-build | scheduled workflow that computes the manifest SHA and dispatches the distribution workflow | Medium |
| Release workflows (12 pipelines) | `workflow_dispatch` workflows with environment protection rules and OIDC to the release account | High |
| Docker (4 pipelines) | `docker/build-push-action` with QEMU/buildx, or the existing multi-arch script on a docker-builder runner | Medium |
| Packer (1 pipeline) | `workflow_dispatch` workflow running `packer build` with OIDC; only relevant while self-hosted EC2 agents exist | Low |
| validate-artifacts | dynamic matrix generated by a setup job (the `manifests.yml` pattern) | Medium |
| Legacy (`jenkins/legacy/**`) | audit first; retire what is dead rather than migrating it | Low |

## 3. How each major job migrates

### 3.1 Distribution builds

`jenkins/opensearch/distribution-build.jenkinsfile` declares three agent labels:

```groovy
AGENT_LINUX_X64   = 'Jenkins-Agent-AL2023-X64-M54xlarge-Docker-Host'
AGENT_LINUX_ARM64 = 'Jenkins-Agent-AL2023-Arm64-M6g4xlarge-Docker-Host'
AGENT_WINDOWS_X64 = 'Jenkins-Agent-Windows2019-X64-M54xlarge-Docker-Host'
```

and fans out inside a single `stage('build') { parallel { ... } }` into `build-and-test-linux-x64-tar`, `-x64-rpm`, `-x64-deb`, `-arm64-tar`, `-arm64-rpm`, `-arm64-deb`, `build-and-test-windows-x64-zip`, `docker build`, and `Trigger-min-snapshot-build`. Each leg calls `buildArchive`/`buildAssembleUpload`/`archiveAssembleUpload` from the shared library and then triggers integ/smoke/bwc tests with the resulting build manifest URL.

Target shape:

```yaml
# .github/workflows/distribution-build-opensearch.yml
on:
  workflow_call:
    inputs:
      input_manifest: {required: true, type: string}
      build_platform: {default: 'linux windows', type: string}
      build_distribution: {default: 'tar rpm deb zip', type: string}
      # ... RC_NUMBER, COMPONENT_NAME, TEST_MANIFEST, BUILD_DOCKER, PARALLEL
  workflow_dispatch:
    inputs: { ... same ... }

jobs:
  get-ci-image-tag:
    uses: ./.github/workflows/get-ci-image-tag.yml
  build:
    needs: get-ci-image-tag
    strategy:
      fail-fast: false
      matrix:
        include:
          - {platform: linux,   arch: x64,   distribution: tar, runner: [self-hosted, linux, x64,   xlarge]}
          - {platform: linux,   arch: x64,   distribution: rpm, runner: [self-hosted, linux, x64,   xlarge]}
          - {platform: linux,   arch: x64,   distribution: deb, runner: [self-hosted, linux, x64,   xlarge]}
          - {platform: linux,   arch: arm64, distribution: tar, runner: [self-hosted, linux, arm64, xlarge]}
          - {platform: linux,   arch: arm64, distribution: rpm, runner: [self-hosted, linux, arm64, xlarge]}
          - {platform: linux,   arch: arm64, distribution: deb, runner: [self-hosted, linux, arm64, xlarge]}
          - {platform: windows, arch: x64,   distribution: zip, runner: [self-hosted, windows, x64]}
    runs-on: ${{ matrix.runner }}
    container: ${{ matrix.platform == 'linux' && fromJson(...) || null }}   # windows legs run without a container
    outputs:
      build-manifest-url: ...
```

Points that need care:

- **Matrix filtering.** `BUILD_PLATFORM`/`BUILD_DISTRIBUTION` are space-separated strings. Either keep them as strings and filter with a setup job that emits a JSON matrix (the `manifests.yml` pattern), or turn them into booleans per leg. A setup job is preferable because it keeps the existing parameter contract.
- **Windows legs cannot use `container:`.** GitHub Actions job containers are Linux-only, so the Windows leg runs steps directly on the runner, as `python-tests.yml` already does with `windows-2022`.
- **Chaining to tests.** `triggerIntegrationTests`/`triggerBWCTests`/`triggerSmokeTests` become downstream jobs with `needs: build` that consume the build manifest URL from a job output, or `uses: ./.github/workflows/integ-test.yml` with `with: build_manifest_url: ...`. Note that a reusable workflow can only be called from a job, not from a step, so any "test this leg as soon as it finishes" behaviour needs one downstream job per matrix leg (matrix outputs do not merge — each leg must publish its manifest URL as an artifact or the fan-out must be duplicated).
- **`Trigger-min-snapshot-build`** becomes a separate job with `if:` on the same condition Jenkins uses today.

The Dashboards pipeline (`jenkins/opensearch-dashboards/distribution-build.jenkinsfile`) has the same shape plus `JOB_NAME_OPENSEARCH = 'distribution-build-opensearch'`, which it uses to construct the OpenSearch build manifest URL. That cross-job URL construction has to be reproduced explicitly, since GitHub Actions has no equivalent of Jenkins' `JOB_NAME`/`BUILD_NUMBER` artifact root.

### 3.2 Integration, BWC, and smoke tests

These take a `BUILD_MANIFEST_URL` and a `TEST_MANIFEST`, download the manifest with `downloadBuildManifest` (31 call sites across the repo), run inside a per-distribution ci-runner image, and call `uploadTestResults` (10 call sites).

Target shape: one reusable workflow per test type, with `workflow_call` inputs matching today's parameters (`TEST_MANIFEST`, `BUILD_MANIFEST_URL`, `COMPONENT_NAME`, `RC_NUMBER`, `TEST_PLATFORM`, `TEST_DISTRIBUTION`) and a `workflow_dispatch` trigger with the same inputs so they stay individually runnable. The 12-hour integ-test timeout requires self-hosted runners; GitHub-hosted jobs are capped at 6 hours.

`integ-test-notification.jenkinsfile` (GitHub issue creation for failures) maps cleanly onto an `if: failure()` job using `actions/github-script`, alongside the existing issue automation in `.github/workflows/`.

### 3.3 gradle-check

`jenkins/legacy/gradle/gradle-check.jenkinsfile` is the hardest single migration:

```groovy
GenericTrigger(
    genericVariables: [ ... pr_from_sha, pr_number, pr_title, pr_owner, gradle_check_command ... ],
    tokenCredentialId: 'jenkins-gradle-check-generic-webhook-token',
    causeString: 'Triggered by PR on OpenSearch core repository'
)
parameterizedCron '''
    H */2 * * * %GIT_REFERENCE=main;AGENT_LABEL=Jenkins-Agent-Ubuntu2404-X64-M58xlarge-Single-Host
    H 6 * * * %GIT_REFERENCE=2.19;AGENT_LABEL=Jenkins-Agent-Ubuntu2404-X64-M58xlarge-Single-Host
'''
throttleJobProperty(maxConcurrentTotal: 45, throttleEnabled: true)
```

It is triggered from a *different* repository (`opensearch-project/OpenSearch`), runs on an m5.8xlarge single-host agent, distinguishes PR / post-merge / timer / user causes, publishes results via `publishGradleCheckTestResults`, archives JUnit XML and jacoco coverage, and sets a rich build description.

Target shape:

- Trigger: `on.repository_dispatch` with `types: [gradle-check]`, carrying the same fields in `client_payload`; the OpenSearch core repo dispatches it with a token. The scheduled runs become two `schedule` entries plus a branch matrix.
- Build cause: `github.event_name` (`repository_dispatch` / `schedule` / `workflow_dispatch`) replaces the `BUILD_CAUSE` string matching, which is more reliable than parsing `currentBuild.getBuildCauses()`.
- Concurrency: `concurrency: { group: gradle-check-${{ github.event.client_payload.pr_number }}, cancel-in-progress: true }` replaces `abortStaleJenkinsJobs`. The global 45-way throttle has no direct equivalent — it becomes a runner-pool sizing problem.
- Results: `currentBuild.description` becomes `$GITHUB_STEP_SUMMARY`; `junit` becomes `actions/upload-artifact` plus a JUnit reporting action; `publishGradleCheckTestResults` (which writes to the OpenSearch metrics cluster) has to be reimplemented as a script in this repo.
- The `GRADLE_CHECK_COMMAND` validation regex must be kept: the input is attacker-influenced (it arrives over a webhook) and is interpolated into a shell command.

### 3.4 check-for-build (orchestration)

```groovy
lock(resource: "CheckForBuild-${INPUT_MANIFEST}-${TARGET_JOB_NAME}", skipIfLocked: true) {
    def sha = getManifestSHA(inputManifest: "manifests/${INPUT_MANIFEST}", jobName: "${TARGET_JOB_NAME}")
    if (sha.exists) { echo "Skipping" } else { build job: "${TARGET_JOB_NAME}", ..., wait: true
                                               buildUploadManifestSHA(...) }
}
```

Target shape: a scheduled workflow with a matrix over the manifest/target pairs currently encoded in the `parameterizedCron` block (3 entries today: `3.9.0` Dashboards, `3.9.0` OpenSearch, `3.8.1` OpenSearch). Move those pairs into a checked-in YAML/JSON file and generate the matrix from it, so adding a release line is a normal PR rather than an edit to a cron string. The SHA check and upload become a small Python entry point in `src/` reusing the existing manifest code. The distribution build is then invoked with `uses:` so the `wait: true` semantics come for free.

### 3.5 Release workflows

`jenkins/release-workflows/` covers promotion (`promote-artifacts`, `promote-repos`, `promote-docker-ecr-lf`, `release-promotion`), publishing (`publish-to-maven-lf`), git operations (`release-branch`, `release-tag`), notes (`release-notes-generate`, `release-notes-check-lf`), and coordination (`release-chores`, `release-schedule`, `release-manifest-commit-lock-lf`).

`release-promotion.jenkinsfile` alone has ~23 parallel promotion stages (deb/rpm/windows × product × arch). These map to a matrix, but the important change is the permission model: each of these jobs assumes an AWS role (`op://opensearch-release-secrets/aws-iam-roles/jenkins-artifact-promotion-role`) against the production account. In GitHub Actions these become `environment: release` jobs with required reviewers and OIDC-assumed roles, so the approval that Jenkins does implicitly by controller access becomes an explicit environment protection rule.

`release-schedule` and `release-chores` are cron/automation jobs with no heavy compute and are good early candidates.

### 3.6 Docker and packer

`jenkins/docker/docker-build-lf.jenkinsfile` runs `docker/ci/build-image-multi-arch.sh` inside a buildx+QEMU ci-runner image on `Jenkins-Agent-Ubuntu2404-X64-M52xlarge-Docker-Builder`. On GitHub Actions this is either the same script on a self-hosted docker-builder runner, or `docker/setup-qemu-action` + `docker/setup-buildx-action` + `docker/build-push-action` on a hosted runner where image size permits. `docker-copy`, `docker-scan`, and `docker-re-release` are thin wrappers and migrate as small `workflow_dispatch` workflows.

`jenkins/packer/packer-build-lf.jenkinsfile` builds the Jenkins agent AMIs from templates in `opensearch-project/opensearch-ci`. If the chosen runner strategy keeps EC2 self-hosted runners, this workflow survives with the same shape (`workflow_dispatch`, OIDC instead of `op://.../packer-build-ids/*`). If the strategy is ARC on Kubernetes, this pipeline is retired and replaced by container image builds.

### 3.7 validate-artifacts

`validate-artifacts-lf.jenkinsfile` builds `validateDistributions` as a map of closures and runs `parallel validateDistributions` over every distribution × architecture combination, choosing a per-distribution image and per-distribution docker args (systemd/privileged for rpm/deb, `-u ContainerAdministrator` for zip). This is exactly the `manifests.yml` dynamic-matrix pattern: a setup job emits the combination list as JSON, the validation job consumes it via `fromJson`, and the per-distribution image and `options` come from the matrix entry.

## 4. Key decisions to be made

These require stakeholder input before implementation starts. Each is a fork in the plan, not a detail.

**(a) Self-hosted runner strategy.** Jenkins today uses m5.4xlarge/m5.8xlarge and m6g.4xlarge EC2 agents, plus Windows Server 2019 agents. Options: Actions Runner Controller (ARC) on EKS with ephemeral pods; EC2 self-hosted runners managed by an autoscaler (e.g. philips-labs/terraform-aws-github-runner) that most closely mirrors today's topology; or GitHub-hosted larger runners where the size and 6-hour cap allow. ARM64 and Windows availability, disk throughput for gradle-check, and who operates the control plane are the deciding factors. This decision blocks 3.1, 3.2, 3.3, and 3.6.

**(b) Secret management.** 23 distinct `op://opensearch-release-secrets/...` references exist across the Jenkinsfiles (GitHub bot credentials, DockerHub staging and production, AWS account IDs, artifact bucket names, the promotion IAM role, the Mend token, packer VPC/subnet/SG/region IDs). Options: keep 1Password as the source of truth and read it with `1password/load-secrets-action` using a service account token; mirror the values into GitHub Secrets (org- or environment-scoped); or eliminate the AWS ones entirely with OIDC role assumption and move the non-credential values (bucket names, account IDs, VPC IDs) into repository variables or a checked-in config. A hybrid — OIDC for AWS, GitHub environment secrets for DockerHub/GitHub bot, variables for the non-secret IDs — is the likely answer but needs security sign-off.

**(c) Replacing `opensearch-build-libraries`.** The Jenkinsfiles pin `jenkins@12.0.0` / `jenkins@12.0.1` / `jenkins@lf-jenkins` from `opensearch-project/opensearch-build-libraries`. Usage counts in this repo: `postCleanup` 148, `withSecrets` 33, `downloadBuildManifest` 31, `detectDockerAgent` 13, `uploadTestResults` 10, `buildArchive` 9, `archiveAssembleUpload` 9, `buildAssembleUpload` 7, `signArtifacts` 4, `runGradleCheck` 3, plus `publishToMaven`, `createReleaseTag`, `buildDockerImage`, `promoteArtifacts`, `getManifestSHA`, `buildUploadManifestSHA`, `publishGradleCheckTestResults`, `abortStaleJenkinsJobs`. For each, choose one of: a composite action under `.github/actions/`; a reusable workflow (only viable for whole-job units such as `runGradleCheck`); a Python entry point in `src/` reusing existing build code; or deletion (`postCleanup` is largely unnecessary on ephemeral runners; `detectDockerAgent` overlaps with `get-ci-image-tag.yml`). The library is Groovy and is unit-tested in its own repository, so this is a rewrite, not a port — and the library is shared with other OpenSearch repositories still on Jenkins, so it cannot simply be retired.

**(d) Cutover style.** Phased (run both systems in parallel per job, compare artifacts, then disable the Jenkins job) versus big-bang per category. Phased costs double compute for the overlap window and needs a rule for which system's artifacts are authoritative; big-bang is cheaper but has no rollback for release-critical jobs. A per-category decision is reasonable: phased for distribution builds and gradle-check, big-bang for lint-style and automation jobs.

**(e) Artifacts, S3, and manifest chaining.** Jenkins jobs upload to S3 under `<job-name>/<version>/<build-number>/<platform>/<arch>/<distribution>/` and downstream jobs consume `BUILD_MANIFEST_URL` built from `JOB_NAME` and `BUILD_NUMBER`. GitHub Actions has no `BUILD_NUMBER` with the same semantics; `github.run_number` is per-workflow and resets if the workflow file is renamed or recreated. Decide the replacement identifier (`github.run_id`, a build-number service, or a manifest-derived ID) before migrating any job that publishes artifacts, because it defines the public URL layout on `ci.opensearch.org` that downstream consumers and documentation depend on.

## 5. Risks

**Shared-library dependency (highest risk).** Every Jenkinsfile begins with `library(identifier: 'jenkins@12.0.0', ...)`. The behaviour of `buildAssembleUpload`, `archiveAssembleUpload`, `signArtifacts`, and `promoteArtifacts` is defined outside this repository and is not fully visible from the Jenkinsfiles. Any migration estimate that does not first read that library is unreliable, and a mistake in reimplementing `signArtifacts` or `promoteArtifacts` is a release-integrity issue, not a CI inconvenience.

**Self-hosted runner capacity and cost.** gradle-check alone is provisioned for 45 concurrent m5.8xlarge-class runs. Distribution builds hold seven large agents for up to four hours. If runner autoscaling is slower or less bursty than today's Jenkins agent pool, PR feedback time on OpenSearch core regresses, and that is visible to every core contributor. Cost modelling has to happen before cutover, not after.

**Secret and credential migration.** The migration touches production DockerHub credentials, the artifact promotion IAM role, and the Maven signing path. Self-hosted runners running untrusted PR code with access to those secrets is a real exposure that Jenkins currently mitigates through job/agent separation; the GitHub Actions equivalent (environment protection rules, `pull_request` vs `pull_request_target`, runner groups scoped to specific workflows) must be designed explicitly rather than inherited.

**Loss of Jenkins-specific features.** Build descriptions (`currentBuild.description`, used to link the PR and the runner in gradle-check), native JUnit trend reporting, throttle categories, and `abortStaleJenkinsJobs` all have approximations rather than equivalents in GitHub Actions. `$GITHUB_STEP_SUMMARY` and third-party test-report actions cover most of it; the historical test-trend view does not exist and, if it matters, needs an external dashboard.

**Concurrency-lock semantics in `check-for-build`.** Jenkins uses `lock(..., skipIfLocked: true)`: if a build for the same manifest and target job is already running, the new one is *skipped*. GitHub Actions `concurrency` either queues one pending run and cancels the rest of the queue, or cancels the in-progress run — neither is "skip silently". Reproducing today's behaviour requires an explicit guard (query the API for an in-progress run of the same workflow with the same inputs and exit early), and getting this wrong causes duplicate distribution builds and duplicate SHA uploads.

**Windows and ARM64 parity.** The Windows legs cannot use job containers, so the Windows toolchain currently baked into the `ci-runner-windows2019-*` image must be reproduced on the runner image or installed per run. ARM64 Linux runners are available (`python-tests.yml` already uses `ubuntu-24.04-arm`) but not at the size class the distribution build needs, so ARM64 depends on decision (a).

## 6. Phased migration approach

The ordering is by blast radius: jobs whose failure delays nobody first, jobs whose failure blocks a release last.

**Phase 0 — foundations (no job moves).** Read `opensearch-build-libraries` and write down the exact contract of each of the ~18 functions used here. Decide (a) runners, (b) secrets, and (e) artifact identity. Stand up a runner pool and prove it with an existing workflow before migrating anything new.

**Phase 1 — stateless checks.** Finish the jobs that are already partly on GitHub Actions and have no secrets, no artifacts, and no self-hosted requirement: `rpm-validation`, remaining lint/manifest checks, and the parts of `release-notes-check` that are pure validation. Low value, but they exercise the new patterns.

**Phase 2 — automation and scheduling.** `release-schedule`, `release-chores`, `gradle-check-flaky-test-issue-creation`, `integ-test-notification`. These are cron and GitHub-API jobs; they validate the `schedule` + `workflow_dispatch` + `repository_dispatch` patterns and the GitHub bot credential path without risking a build.

**Phase 3 — validation and docker.** `validate-artifacts` (proves the dynamic-matrix pattern against real artifacts), then `docker-copy`, `docker-scan`, and `docker-build` for staging images only. First use of registry credentials.

**Phase 4 — gradle-check.** Highest-volume job and the one that most exercises the runner pool. Run it in parallel with Jenkins against the same PRs, compare pass/fail and duration for a full release cycle, then cut the webhook over. Keep the Jenkins job dormant but restorable.

**Phase 5 — distribution builds and tests.** `feature-build` first (lowest consumer impact), then `distribution-build-opensearch`, then Dashboards, then `integ-test`/`bwc-test`/`smoke-test`, then `check-for-build`. Artifacts must be byte-comparable with the Jenkins output before the Jenkins job is disabled.

**Phase 6 — release workflows.** `promote-artifacts`, `promote-repos`, `promote-docker-ecr`, `publish-to-maven`, `release-branch`, `release-tag`, `release-promotion`. Last, behind environment protection rules, and ideally rehearsed against a staging account for a full release candidate before a real release depends on them.

**Phase 7 — decommission.** Retire `jenkins/legacy/**`, the Jenkins controller, and `packer-build` if the runner strategy no longer needs AMIs. Keep the Jenkinsfiles in git history; the pipelines encode a decade of accumulated release behaviour that is worth being able to read.
