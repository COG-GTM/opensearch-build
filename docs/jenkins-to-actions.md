# Jenkins to GitHub Actions migration: `distribution-build` and `integ-test`

This document describes the migration of two Jenkins pipelines to GitHub Actions:

| Jenkins pipeline | GitHub Actions entry point |
| --- | --- |
| `jenkins/opensearch/distribution-build.jenkinsfile` | `.github/workflows/distribution-build-opensearch.yml` |
| `jenkins/opensearch/integ-test.jenkinsfile` | `.github/workflows/integ-test.yml` |

The migration is **additive**: nothing under `jenkins/` or `tests/jenkins/` was changed, so the Jenkins source and
the Actions target can be reviewed side by side. Every new workflow is `workflow_dispatch` only (the two reusable
workflows are `workflow_call` only), so nothing runs on `push` or `pull_request` in this fork.

The custom steps used by these two pipelines come from the shared library
[`opensearch-project/opensearch-build-libraries`](https://github.com/opensearch-project/opensearch-build-libraries)
(`vars/*.groovy`, `src/jenkins/*.groovy`). They were converted to composite actions under `.github/actions/`, not
inlined, so the mapping stays reviewable one step at a time.

Line references below point at the Jenkins sources as they exist in this repository at the time of the migration.

## 1. Files added

### Workflows

| File | Trigger | Role |
| --- | --- | --- |
| `.github/workflows/distribution-build-opensearch.yml` | `workflow_dispatch` | Orchestrator for `distribution-build.jenkinsfile`: parameter validation, matrix generation, post-build publishing. |
| `.github/workflows/distribution-build-archive.yml` | `workflow_call` | One `tar`/`zip` build stage: `build` + `assemble` + `upload` on a single runner. |
| `.github/workflows/distribution-build-package.yml` | `workflow_call` | One `rpm`/`deb` build stage: archive build job, then a separate assemble job (Jenkins used two agents). |
| `.github/workflows/integ-test.yml` | `workflow_dispatch` | Full port of `integ-test.jenkinsfile`, including the per-component fan-out. |

### Composite actions (`.github/actions/<step-name>/action.yml`)

| Composite action | Ported from |
| --- | --- |
| `detect-docker-agent` | `vars/detectDockerAgent.groovy` |
| `detect-test-docker-agent` | `vars/detectTestDockerAgent.groovy` |
| `build-manifest` | `vars/buildManifest.groovy` (`./build.sh`) |
| `assemble-manifest` | `vars/assembleManifest.groovy` (`./assemble.sh`) |
| `build-archive` | `vars/buildArchive.groovy` |
| `build-assemble-upload` | `vars/buildAssembleUpload.groovy` |
| `assemble-upload` | `vars/assembleUpload.groovy` |
| `archive-assemble-upload` | `vars/archiveAssembleUpload.groovy` |
| `upload-artifacts` | `vars/uploadArtifacts.groovy` (+ `BuildManifest` URL accessors) |
| `upload-to-s3` | `vars/uploadToS3.groovy` |
| `download-from-s3` | `vars/downloadFromS3.groovy` |
| `upload-index-file` | `vars/uploadIndexFile.groovy` |
| `retrieve-previous-build` | `vars/retrievePreviousBuild.groovy` |
| `sign-artifacts` | `vars/signArtifacts.groovy` (rpm branch only) |
| `build-yum-repo` | `vars/buildYumRepo.groovy` |
| `download-build-manifest` | `vars/downloadBuildManifest.groovy` |
| `run-integ-test-script` | `vars/runIntegTestScript.groovy` |
| `upload-test-results` | `vars/uploadTestResults.groovy` |
| `create-upload-test-report-manifest` | `vars/createUploadTestReportManifest.groovy` |
| `publish-distribution-build-results` | `vars/publishDistributionBuildResults.groovy` + `vars/buildMessage.groovy` |
| `publish-integ-test-results` | `vars/publishIntegTestResults.groovy` |
| `index-metrics-records` | the `indexFailedTestData()` / `indexTestFailuresData()` helpers shared by the two `publish*` steps |
| `update-build-failure-issues` | `vars/updateBuildFailureIssues.groovy` + `vars/createGithubIssue.groovy` + `vars/closeGithubIssue.groovy` |
| `trigger-downstream-tests` | `triggerIntegrationTests()` / `triggerSmokeTests()` / `triggerBWCTests()` (`distribution-build.jenkinsfile:1074-1131`) |

### Helper scripts

The Jenkins pipelines lean on Groovy model classes (`lib.jenkins.InputManifest`, `lib.jenkins.BuildManifest`,
`lib.jenkins.TestManifest`, `lib.jenkins.ComponentBuildStatus`) for manifest parsing and URL construction. Those
accessors were reimplemented in Python rather than duplicated across shell snippets:

| Script | Replaces |
| --- | --- |
| `.github/scripts/manifest_paths.py` | `InputManifest` / `BuildManifest` / `TestManifest` accessors: docker agent selection, artifact roots and URLs, the build matrices, the integ-test component list, `test.sh --paths`/`--base-path`. |
| `.github/scripts/metrics_records.py` | The OpenSearch metrics documents built by `publishDistributionBuildResults.groovy` and `publishIntegTestResults.groovy`. |
| `.github/scripts/build_failure_issues.py` | The issue create/close decision logic of `updateBuildFailureIssues.groovy`. |
| `.github/mappings/*.json` | The index mappings the Jenkins steps posted alongside their metrics documents. |

## 2. Construct mapping

### Pipeline-level

| Jenkins construct | Source | GitHub Actions equivalent |
| --- | --- | --- |
| `pipeline { agent none }` | `distribution-build.jenkinsfile:23-27` | No workflow-level runner; every job declares `runs-on`. |
| `options { timeout(time: 4, unit: 'HOURS') }` | `distribution-build.jenkinsfile:25` | `timeout-minutes: 240` on every build job (Actions has no workflow-level timeout). |
| `options { timeout(time: 12, unit: 'HOURS') }` | `integ-test.jenkinsfile:30` | `timeout-minutes: 720` on the component jobs. |
| `options { buildDiscarder(logRotator(daysToKeepStr: '60')) }` | `integ-test.jenkinsfile:31` | Not migrated, see [§5](#5-not-migrated--needs-decisions). |
| `environment { GRADLE_OPTS = ... }` | `distribution-build.jenkinsfile:28-33` | Workflow-level `env:`. |
| `parameters { ... }` | `distribution-build.jenkinsfile:34-137`, `integ-test.jenkinsfile:39-65` | `workflow_dispatch.inputs` (see [§3](#3-parameters)). |
| `agent { label AGENT_LINUX_X64 }` | `distribution-build.jenkinsfile:30-32` | `runs-on` (`ubuntu-24.04`, `ubuntu-24.04-arm`, `windows-2022`), resolved in `RUNNERS` in `manifest_paths.py`. |
| `agent { docker { image ... args ... } }` | `distribution-build.jenkinsfile:249-259` | Job-level `container: { image, options }`, resolved from the manifest by `detect-docker-agent`. |
| `stage('...') { parallel { ... } }` | `distribution-build.jenkinsfile:216-957` | `strategy.matrix` with `fail-fast: false`. |
| Dynamic `parallel` map over components | `integ-test.jenkinsfile:203-281` | `strategy.matrix.component` fed by `fromJSON()` of a job output. |
| `when { expression { params.BUILD_PLATFORM.contains('linux') } }` | e.g. `distribution-build.jenkinsfile:240-248` | Matrix entries are filtered when generated (`manifest_paths.py distribution-matrix`); Actions cannot skip a matrix entry from inside the job. |
| `post { always { ... } }` | `distribution-build.jenkinsfile:1009-1032`, `integ-test.jenkinsfile:282-329` | A dependent job (or step) with `if: always()`. |
| `post { success { ... } }` | `distribution-build.jenkinsfile:1033-1043` | `if: success()`, or `if: needs.<job>.result == 'success'` for job-level posts. |
| `post { failure { ... } }` | `distribution-build.jenkinsfile:1053-1061` | `if: failure()`. |
| `post { unstable { ... } }` | `distribution-build.jenkinsfile:1044-1052` | No equivalent, see [§5](#5-not-migrated--needs-decisions). |
| `stash` / `unstash` | `distribution-build.jenkinsfile:355-360, 384-391` | `actions/upload-artifact` and `actions/download-artifact` between the two jobs of `distribution-build-package.yml`. |
| `retry(3)` around the Windows stage | `distribution-build.jenkinsfile:897-957` | Documented, see [§5](#5-not-migrated--needs-decisions); Actions has no step-level retry. |
| `sleep` staggering of parallel branches | `integ-test.jenkinsfile:205-209` | A `sleep $(( job-index * 20 ))` first step in the component job, so the port keeps the same S3 rate-limit behavior. |
| `currentBuild.startTimeInMillis`, `BUILD_NUMBER`, `BUILD_URL` | throughout | `github.run_number` / `github.run_id` plus a `build-start-time` output recorded in `verify-parameters`. |
| `postCleanup()` | `distribution-build.jenkinsfile:1030` | Not needed: Actions runners and job containers are ephemeral. |

### Credentials

| Jenkins construct | Source | GitHub Actions equivalent |
| --- | --- | --- |
| `withCredentials([usernamePassword(...)])` for the GitHub bot | `distribution-build.jenkinsfile:18-22` | `secrets.GITHUB_BOT_TOKEN`, passed to `update-build-failure-issues` as `github-token`. |
| `withSecrets(secrets: ...)` reading 1Password refs | `distribution-build.jenkinsfile:18-22`, `integ-test.jenkinsfile:24-27` | Repository/environment secrets, listed in [§4](#4-required-secrets-and-aws-configuration). |
| `withAWS(role: '...', roleAccount: '...', duration: ...)` | `vars/uploadToS3.groovy`, `vars/downloadFromS3.groovy`, `vars/uploadTestResults.groovy` | `permissions: id-token: write` plus `aws-actions/configure-aws-credentials` (OIDC) inside the composite action that needs it. |
| `signArtifacts(sigtype: '.rpm', ...)` | `distribution-build.jenkinsfile:397-404` | `sign-artifacts`, which assumes the signing role over OIDC; no key material is referenced in the repository. |

### Shared-library steps that became job-level workflows

Two library steps are honest *job* boundaries rather than steps, because Jenkins ran their halves on different
agents; they became reusable workflows:

| Jenkins stage shape | Reusable workflow |
| --- | --- |
| `buildAssembleUpload()` on one docker agent (`distribution-build.jenkinsfile:239-321`, `:560-639`, `:880-957`) | `distribution-build-archive.yml` |
| `buildArchive()` on one agent, `archiveAssembleUpload()` on a second agent with a different image (`distribution-build.jenkinsfile:322-440`, `:441-559`, `:640-758`, `:759-879`) | `distribution-build-package.yml` (`build-archive` job then `assemble` job) |

## 3. Parameters

`workflow_dispatch` accepts at most **10 inputs**, while `distribution-build.jenkinsfile:34-137` declares 19
parameters. The frequently changed parameters kept dedicated inputs; the remaining booleans and strings are packed
into a single space-separated `OPTIONS` input that the `verify-parameters` job unpacks into job outputs:

```text
OPTIONS: continue-on-error incremental update-latest-url update-github-issue parallel=4 \
         previous-build-id=latest build-docker=build_docker
```

* A bare token sets the flag to `true`; prefixing it with `no-` sets it to `false` (`no-incremental`).
* `key=value` tokens carry the string parameters.
* An unknown token fails the run rather than being silently ignored, and `parallel`, `previous-build-id` and
  `build-docker` values are checked against the shapes the Jenkins parameters allowed.
* The parser starts from the **Jenkins defaults** (`distribution-build.jenkinsfile:104-137`: `continue-on-error`,
  `incremental`, `update-latest-url` and `update-github-issue` on, `parallel=4`, `previous-build-id=latest`), so
  dispatching `OPTIONS: parallel=8` changes only the worker count instead of quietly turning the other four off.
* `sign-rpm` is the one flag with no Jenkins parameter behind it: `assembleManifest()` signed rpm packages
  unconditionally, which a fork without signing keys cannot do, so it defaults to off and has to be requested.

| Jenkins parameter | `distribution-build-opensearch.yml` |
| --- | --- |
| `INPUT_MANIFEST` | input `INPUT_MANIFEST` |
| `TEST_MANIFEST` | input `TEST_MANIFEST` |
| `INTEG_TEST_JOB_NAME` | input `INTEG_TEST_WORKFLOW` |
| `BUILD_PLATFORM`, `BUILD_DISTRIBUTION` | inputs of the same name |
| `TEST_PLATFORM`, `TEST_DISTRIBUTION` | inputs of the same name |
| `RC_NUMBER` | input `RC_NUMBER` |
| `BUILD_NUMBER` | not needed: `github.run_number` |
| `CONTINUE_ON_ERROR` | `OPTIONS: continue-on-error` |
| `SKIP_ARTIFACT_CHECK` | `OPTIONS: skip-artifact-check` |
| `INCREMENTAL`, `PREVIOUS_BUILD_ID` | `OPTIONS: incremental`, `previous-build-id=` |
| `UPDATE_LATEST_URL` | `OPTIONS: update-latest-url` |
| `UPDATE_GITHUB_ISSUE` | `OPTIONS: update-github-issue` |
| `BUILD_DOCKER` | `OPTIONS: build-docker=` |
| no Jenkins equivalent (`assembleManifest()` always signed rpm) | `OPTIONS: sign-rpm`, default off |
| `PARALLEL` / gradle workers | `OPTIONS: parallel=` |
| `SMOKE_TEST_JOB_NAME`, `BWC_TEST_JOB_NAME` | not migrated, see [§5](#5-not-migrated--needs-decisions) |

`integ-test.jenkinsfile:39-65` has five parameters, so `integ-test.yml` maps them one to one
(`COMPONENT_NAME`, `TEST_MANIFEST`, `BUILD_MANIFEST_URL`, `RC_NUMBER`, `VALIDATE_ARTIFACTS`) and adds one input:
`DISTRIBUTION_BUILD_URL`, because Jenkins derived the `distributionBuildUrl` metrics field from the *upstream*
distribution build (`integ-test.jenkinsfile:316`), which a dispatched workflow cannot discover on its own.
`trigger-downstream-tests` fills it with the run URL of the distribution build; for a manual dispatch it can be left
empty, and the build manifest URL identifies the build under test instead.

Both `verify-parameters` jobs validate the dispatch inputs against a character allow-list before anything else runs.
Every input reaches a shell command or a `docker run` option somewhere downstream (manifest paths, component lists,
platform and distribution lists), so the values are constrained once, centrally, and every step reads them from `env`
rather than having `${{ ... }}` expanded into the middle of a bash script.

`COMPONENT_NAME` and `OPTIONS: incremental` are mutually exclusive: `--component` and `--incremental` are in one
mutually exclusive group in `src/build_workflow/build_args.py`, so `build.sh` rejects the pair. Jenkins passed both
parameters straight through and failed inside `build.sh` minutes into the job; `build-manifest` now fails with an
explicit error before the build starts.

## 4. Required secrets and AWS configuration

No secret values are committed. The workflows reference the following **repository or environment secrets**, which
have to be created before a run can succeed. The Jenkins equivalents are the 1Password references in
`distribution-build.jenkinsfile:18-22` and `integ-test.jenkinsfile:24-27`.

| Secret | Used by | Jenkins source |
| --- | --- | --- |
| `AWS_ACCOUNT_PUBLIC` | `upload-to-s3`, `upload-index-file`, `upload-test-results`, `create-upload-test-report-manifest` | `op://opensearch-release-secrets/aws-accounts/jenkins-aws-account-public` |
| `ARTIFACT_BUCKET_NAME` | the same steps | `op://opensearch-release-secrets/aws-resource-arns/jenkins-artifact-bucket-name` |
| `AWS_ACCOUNT_ARTIFACT` / `ARTIFACT_PRODUCTION_BUCKET_NAME` | `retrieve-previous-build`, `download-from-s3` | the artifact promotion account and bucket |
| `ARTIFACT_PROMOTION_ROLE_NAME` | `retrieve-previous-build`, `sign-artifacts` | `op://opensearch-release-secrets/aws-iam-roles/jenkins-artifact-promotion-role` |
| `RPM_SIGNING_ACCOUNT` | `sign-artifacts` | the RPM signing account used by `signArtifacts()` |
| `RPM_RELEASE_SIGNING_PASSPHRASE_SECRETS_ARN`, `RPM_RELEASE_SIGNING_SECRET_KEY_ID_SECRETS_ARN`, `RPM_RELEASE_SIGNING_KEY_ID`, `RPM_SIGNING_PASSPHRASE_SECRETS_ARN`, `RPM_SIGNING_SECRET_KEY_ID_SECRETS_ARN`, `RPM_SIGNING_KEY_ID` | `sign-artifacts`, exported as job-level `env` by the `assemble` job of `distribution-build-package.yml` | `op://opensearch-release-secrets/rpm-signing/*`, read inside `withSecrets()` by `signArtifacts.groovy` |
| `METRICS_HOST_URL`, `METRICS_HOST_ACCOUNT` | `publish-distribution-build-results`, `publish-integ-test-results`, `update-build-failure-issues` | the metrics cluster endpoint and account |
| `GITHUB_BOT_TOKEN` | `update-build-failure-issues` | `op://opensearch-infra-secrets/github-bot/*` |

AWS access uses OIDC rather than static keys:

1. Add `token.actions.githubusercontent.com` as an OIDC provider in each AWS account.
2. Create a role per account whose trust policy allows `repo:COG-GTM/opensearch-build:*` (tighten to
   `ref:refs/heads/main` or an environment as appropriate) and whose permissions match the Jenkins roles
   (`opensearch-bundle` for artifact upload, the promotion role for signing/downloads, the metrics role for
   OpenSearch indexing).
3. The jobs already declare `permissions: id-token: write` and call
   `aws-actions/configure-aws-credentials` with `role-to-assume` built from the account and role-name secrets.

`trigger-downstream-tests` calls `gh workflow run`, so the calling job declares `permissions: actions: write`. The
default `GITHUB_TOKEN` can dispatch a `workflow_dispatch` event with that permission, but the dispatched workflow
must already exist on the default branch. Swap in a PAT or GitHub App token secret if the downstream workflow lives
in another repository, which is what the Jenkins `build job:` calls do today.

Runner labels are the GitHub-hosted defaults (`ubuntu-24.04`, `ubuntu-24.04-arm`, `windows-2022`). The Jenkins
labels are `m5.4xlarge`/`m6g.4xlarge` docker hosts; a real migration should point `RUNNERS` in
`.github/scripts/manifest_paths.py` at self-hosted runners of comparable size, because hosted runners cannot build
the full distribution.

## 5. Not migrated / needs decisions

| Jenkins behavior | Source | Status and recommendation |
| --- | --- | --- |
| `markStageUnstableIfPluginsFailedToBuild()` | `distribution-build.jenkinsfile:311-315, 1067-1071` | **Ported as a warning, open decision.** Actions has no result between success and failure, so a build whose plugins failed is a green run with a `::warning::` annotation. The practical consequence is that `update-latest-url` still publishes `index.json` (making `latest` point at an incomplete build) and the downstream integ tests still fire — under Jenkins the stage was UNSTABLE, and `post { unstable }` did the same two things, so this matches, but if the intent is that an incomplete build must not become `latest`, the fix is to fail the build step when the log contains `Failed plugins are` and drop `--continue-on-error`. |
| rpm signing shares a runner with `assemble.sh` | `signArtifacts.groovy`, `distribution-build.jenkinsfile:373-376` | **Ported as-is, open decision.** Jenkins wrapped the whole assemble stage in `withSecrets(rpm-signing/*)`, so the signing secret ARNs and the signing role were available to the packaging code of every component. `distribution-build-package.yml` reproduces that: the ARNs are job-level `env` and `id-token: write` is job scoped, so any step of the assemble job can assume `jenkins-prod-rpm-signing-assume-role`. Closing it means signing in a separate job that only downloads, signs and re-uploads the packages — a departure from the Jenkins stage layout, hence a decision rather than a fix. |
| `buildDiscarder(logRotator(daysToKeepStr: '60'))` | `integ-test.jenkinsfile:31` | **No equivalent.** Actions retention is a repository/organization setting (`Settings > Actions > Artifact and log retention`), not per workflow. Set retention there, and `retention-days` on individual `upload-artifact` steps. |
| `post { unstable { ... } }` and `currentBuild.result = 'UNSTABLE'` | `distribution-build.jenkinsfile:1044-1052, 1067-1073` | **No equivalent.** Actions has only success/failure/cancelled. `markStageUnstableIfPluginsFailedToBuild()` (a pipeline-local helper, not a library step) greps the stage log for `Failed plugins are` and downgrades the stage to UNSTABLE. `build-manifest` tees `build.sh` output into `build.log` and greps it for the same string, emitting a `::warning title=Plugins failed to build::` annotation on the job; if "unstable" must be actionable, a check run with a neutral conclusion is the closest model. |
| `retry(3)` plus the scoop JDK reset in the Windows stage | `distribution-build.jenkinsfile:897-957` | **Behavior ported, retry not.** The zip stage runs directly on the Windows runner (no container). Actions has no step-level retry; either re-run the failed job from the UI, wrap the step in a PowerShell retry loop, or use `nick-fields/retry`. Deliberately left out rather than hidden inside a bash loop. |
| `lock` resources | not used by these two pipelines | For pipelines that do use it, `concurrency: { group: ..., cancel-in-progress: false }` is the equivalent; note it queues *one* pending run and cancels the rest, unlike Jenkins' FIFO lock queue. |
| `input` / manual approval | not used by these two pipelines | Use a deployment `environment` with required reviewers on the job that needs approval. |
| `build job: 'publish-opensearch-min-snapshots'` | `distribution-build.jenkinsfile:218-238` | **Reported, not triggered.** That pipeline is outside this migration slice; the job prints a notice with the parameters Jenkins would have passed. |
| `build job: 'docker-build'` / `docker-copy` / `docker-scan` | `distribution-build.jenkinsfile:958-1008` | **Reported, not triggered.** Same reason. The docker job in the port resolves the linux x64/arm64 tar artifact URLs that `buildDockerImage.groovy` consumes and prints them together with the `buildOption` Jenkins would have passed, instead of polling `env.ARTIFACT_URL_*` in a `while (true) { sleep 120 }` loop (`distribution-build.jenkinsfile:983-1000`). |
| `triggerSmokeTests()` / `triggerBWCTests()` | `distribution-build.jenkinsfile:1091-1122` | **Reported, not triggered.** `smoke-test` and `bwc-test` are not in scope; `trigger-downstream-tests` triggers the integ-test workflow and annotates what else Jenkins would have started, keeping the Jenkins skip conditions (empty job name/manifest/URL, platform or distribution not in `TEST_PLATFORM`/`TEST_DISTRIBUTION`). |
| `triggerDistributionValidationWorkflow()` / `triggerNightlyPlayground()` | `distribution-build.jenkinsfile:1123-1145` | **Reported, not triggered.** Both only fire for release candidates (`RC_NUMBER > 0`) and live in other repositories/pipelines. |
| `build job: 'distribution-validation'` for `VALIDATE_ARTIFACTS` | `integ-test.jenkinsfile:108-135` | **Reported, not triggered.** The `validate-artifacts` job prints the parameters; migrating `distribution-validation` is a separate slice. |
| `UPDATE_GITHUB_ISSUES` parameter of the integ-test job, and `updateIntegTestFailureIssues()` | `distribution-build.jenkinsfile:1086`, `vars/updateIntegTestFailureIssues.groovy` | **Not migrated, and dead in Jenkins too.** `triggerIntegrationTests()` passes `UPDATE_GITHUB_ISSUES=true`, but `integ-test.jenkinsfile:39-65` declares no such parameter and never reads it: the per-component integ-test autocut runs from the separate release pipeline that calls `updateIntegTestFailureIssues()`, which needs the release metrics indices and the markdown release table. `gh workflow run` rejects an undeclared `--field`, so `trigger-downstream-tests` does not pass it and `integ-test.yml` does not declare it. Migrating that release pipeline is a separate slice; the pattern would mirror `update-build-failure-issues`. |
| `build job: 'integ-test-notification', wait: false` | `integ-test.jenkinsfile:330-345` | **Reported, not triggered.** Notifications depend on Jenkins-only credentials; the recommended pattern is a small `notify` job posting to Slack via a webhook secret. |
| rpm signing | `distribution-build.jenkinsfile:397-404`, `vars/signArtifacts.groovy` | **Ported, disabled by default.** `sign-rpm` defaults to `false`, matching a fork that has no signing keys. The six 1Password values are exported as job-level `env` from repository secrets ([§4](#4-required-secrets-and-aws-configuration)); when `sign-rpm` is `true` and any of them is empty, `sign-artifacts` fails with an explicit error instead of producing unsigned-but-reported packages. The mac, windows, `jar_signer` and PGP branches of `signArtifacts()` are unreachable from these two pipelines and were not ported. |
| Container options from the manifest | `manifests/*/opensearch-*-test.yml` `ci.image.args` | **Ported with the unsupported options stripped.** `sanitize_container_options()` in `.github/scripts/manifest_paths.py` drops `--entrypoint` and `--network` from the manifest args before they reach `jobs.<id>.container.options` (Actions documents both as unsupported, and the runner appends its own `--entrypoint tail -f /dev/null` regardless), and it fails the parse on a double quote, which would otherwise break out of the `fromJSON(format(...))` container object. The consequence is real and needs a decision: the rpm/deb test images are built to boot `--entrypoint=/usr/lib/systemd/systemd` so that `systemctl` works inside the container, and that cannot be reproduced with a job container. Those distributions therefore need one of: running the tests without a job container and doing an explicit `docker run --privileged --entrypoint=/usr/lib/systemd/systemd` from a step, a systemd-enabled self-hosted runner, or a test image that does not depend on PID 1 being systemd. `--privileged`, `-u root` and the cgroup bind mounts are passed through as-is, but `docker create` + `docker exec` is not identical to Jenkins' `docker.image().inside()` and stays unverified without a live dispatch. The way the container is disabled for Windows jobs is unverified for the same reason: `container: ${{ ... && fromJSON(...) || null }}` relies on a `null` expression result being read as "no container" (`distribution-build-archive.yml:118`, `integ-test.yml:222`, `:308`); `|| ''` is the more commonly documented spelling if Actions renders the null as an empty image name. |
| Metrics publication | `vars/publishDistributionBuildResults.groovy`, `vars/publishIntegTestResults.groovy` | **Ported, unverified against a live cluster.** The documents and index mappings were reconstructed from the Groovy sources (`.github/scripts/metrics_records.py`, `.github/mappings/*.json`); they cannot be validated without the metrics cluster, so treat the schema as proposed rather than confirmed. |
| GitHub issue create/close | `vars/updateBuildFailureIssues.groovy`, `createGithubIssue.groovy`, `closeGithubIssue.groovy` | **Ported, unverified.** `update-build-failure-issues` queries the metrics cluster for component build failures and then uses `gh issue create/close`, including the `daysToReOpen=3` window of `createGithubIssue.groovy:28` that reopens a recently closed autocut issue instead of cutting a duplicate. An issue closed longer ago than that is deliberately not reused, exactly as in Jenkins. `--repo` receives the manifest `repository` value (a `https://github.com/...git` URL) unchanged, which is what the Groovy steps passed as well. Without the metrics cluster the query cannot be exercised, and the step is only enabled when `OPTIONS: update-github-issue` is set. |
| Agent-specific state (workspace reuse, `postCleanup()`, `/tmp/workspace` sizing, docker-in-docker) | throughout both pipelines | **No equivalent needed, but capacity matters.** Every Actions job starts clean, so any implicit reliance on a warm workspace or a pre-pulled image becomes a fresh download. Self-hosted runners with a persistent gradle/maven cache (or `actions/cache`) are the recommended replacement for the warm Jenkins agents. |
| Jenkins credentials binding via 1Password (`withSecrets`) | `distribution-build.jenkinsfile:18-22` | Replaced by GitHub secrets ([§4](#4-required-secrets-and-aws-configuration)). If 1Password remains the source of truth, `1password/load-secrets-action` keeps the indirection instead of copying values into GitHub. |

## 6. Known smells kept on purpose

Fidelity was preferred over cleanliness; these are ported as-is and flagged rather than fixed:

* **Component staggering by `sleep`.** `integ-test.jenkinsfile:205-209` sleeps `index * 20` seconds per parallel
  branch to avoid S3 throttling. The port does the same instead of adding real rate limiting.
* **Distribution-specific test exclusions.** `cross-cluster-replication` and `query-insights` are skipped for
  `rpm`/`deb` (`integ-test.jenkinsfile:163-170`, upstream issue #4610). The exclusion list is hard-coded in
  `manifest_paths.py` exactly as in Jenkins.
* **rpm/deb tests run as uid 1000.** `integ-test.jenkinsfile:152` switches the user for those distributions only;
  `run-integ-test-script` keeps the switch behind the same condition.
* **The build matrix is a hand-written stage list.** Jenkins declares seven near-identical stages
  (`distribution-build.jenkinsfile:239-957`); `BUILD_TARGETS` in `manifest_paths.py` mirrors that list, including
  the fact that the arm64 rpm stage is pinned to the x64 agent label (`distribution-build.jenkinsfile:650`).
* **The rpm/deb build stage mixes images.** `distribution-build.jenkinsfile:337-339` builds the archive on the
  *tar* image but with the *package* docker args, and `:373-376` runs the assemble stage on the package image with no
  args at all. `manifest_paths.py` reproduces that pairing (`image`/`args` for the build job, `assemble-image`/empty
  `assemble-args` for the assemble job) rather than making the two stages consistent.
* **A component with no security config counts as passed.** `publishIntegTestResults.groovy:67-69` defaults a
  missing `with-security`/`without-security` status to `unknown`, which is not `fail`, so the component is recorded as
  `passed`; `metrics_records.py` keeps that. The Groovy also compares the already-lowercased status against
  `'Not Available'`, a comparison that can never be true — the port compares against `not available`, which is the only
  place where it fixes an upstream bug instead of reproducing it.
* **Release-candidate promotion is not gated on `RC_NUMBER`.** `uploadArtifacts.groovy:49-58` promotes the min
  tarball and the package into `release-candidates/` for every non-feature build, RC or not, and only skips feature
  builds. `upload-artifacts` keeps that behavior; the promotion steps additionally require the production bucket, the
  production account and the promotion role name to be configured, so a fork without those secrets never writes to the
  production bucket at all.
* **The test java version ignores the distribution.** `runIntegTestScript.groovy:32` calls `detectTestDockerAgent()`
  without a platform or distribution, so `JAVA_HOME` always comes from the linux/tar image and is only exported for
  the OpenSearch core distribution off Windows. Both quirks are kept.
* **`TEST_PLATFORM` / `TEST_DISTRIBUTION` are substring matched.**
  `distribution-build.jenkinsfile:1075` skips the downstream tests with `!TEST_PLATFORM.contains(platform)`, a plain
  substring test, so a `TEST_DISTRIBUTION` of `tar.gz` also enables the `tar` stage. `trigger-downstream-tests` keeps
  the substring semantics (`case "${TEST_PLATFORM}" in *"${PLATFORM}"*`) instead of matching whole words, so the port
  triggers exactly the same set of downstream runs.
* **`RC_NUMBER` forces the docker tag.** `distribution-build.jenkinsfile:205-208` overrides `BUILD_DOCKER` for
  release candidates; the port reproduces the override rather than making the caller pass a consistent pair.

## 7. Validation performed

These workflows cannot be executed here: they need the OpenSearch build infrastructure, AWS accounts and the
metrics cluster. What was checked:

* `actionlint` is clean for all new workflows.
* An empty component list is possible: every component of the test manifest can be missing from the build manifest,
  or excluded for rpm/deb. A matrix cannot be empty, so `verify-parameters` publishes a `has-components` flag and the
  matrix job is gated on it, leaving a warning in the run summary instead of an invalid-matrix failure.
* Temporary AWS credentials are handed to `curl` through a `0600` config file (`--config`) rather than
  `--user`/`-H` arguments, so they never appear in the process table of a runner that also executes build and test
  code.
* `yamllint` (repository configuration) reports no errors for the new workflows, composite actions and mappings.
* `flake8`, `isort` and `mypy` are clean for `.github/scripts/`.
* `manifest_paths.py` was run against `manifests/3.9.0/opensearch-3.9.0.yml` and
  `manifests/3.9.0/opensearch-3.9.0-test.yml` to confirm the docker agent selection, the generated matrices, the
  artifact URLs and the integ-test component list match what the Groovy accessors produce.
* No workflow has a `push` or `pull_request` trigger.
