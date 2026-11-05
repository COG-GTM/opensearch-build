# ADR 0001: GitHub Actions runners and secrets

* Status: accepted
* Plan step: s1.3 (feeds s2.1, s2.2, s2.4 and every p3–p5 port)
* Inputs: the **Agent labels** and **Credentials** columns of [`docs/jenkins-gha-inventory.md`](../jenkins-gha-inventory.md) (s1.1), and the `withAWS` / `withSecrets` calls inside `opensearch-project/opensearch-build-libraries` (`vars/*.groovy`, default branch) for the library-step credentials the inventory truncates (`+N more`).
* Already decided, not reopened here: `d-runners` = **ephemeral self-hosted runners on AWS from the Jenkins AMIs**.

## Context

Every live Jenkins pipeline runs on a labelled EC2 agent class (AL2023 x64/arm64 Docker hosts, an Ubuntu 24.04 Docker builder, Windows 2019, macOS 14 x64/arm64, single-host systemd test hosts and dedicated benchmark hosts) and pulls its build environment from a `docker/ci` CI-runner image. Credentials come from three places: `op://opensearch-release-secrets/...` 1Password references read by `withSecrets`, Jenkins credential IDs (`jenkins-github-bot-token`, `jenkins-integ-test-webhook`, GenericTrigger tokens), and `withAWS(role:, roleAccount:)` role assumptions from the agent's instance profile.

GitHub Actions has to keep three things identical: the hardware/OS a job runs on, the container image it builds in, and what it is allowed to touch in AWS and GitHub. Hosted runners fail the first two (no arm64 Docker hosts with these AMIs, no macOS 14 x64 with our toolchain, 6 h job cap vs. the 24 h benchmark timeout), which is why `d-runners` picked self-hosted runners from the existing AMIs.

## Decision

### 1. Runners

1. **One runner per job.** Runners register with a just-in-time (`--ephemeral` / JIT config) registration, run exactly one job and the instance is terminated. Nothing (workspace, Docker cache, AWS session, `~/.gitconfig`) survives between jobs; this replaces Jenkins' `postCleanup` and `cleanWs`.
2. **Same AMIs as Jenkins.** Instances launch from the AMIs that `jenkins/packer/packer-build-lf.jenkinsfile` builds from the `opensearch-ci` packer templates (ported in s3.5 as `packer-build.yml`). The runner agent and a boot script that fetches the JIT config are the only additions; the Jenkins agent JAR is not started. Instance types stay as the Jenkins label names them.
3. **Scale from zero on `workflow_job` events.** s2.4 provisions this with a maintained autoscaler for ephemeral EC2 runners (the `github-aws-runners/terraform-aws-github-runner` Terraform module unless s2.4 records a better fit), one runner pool per row of the label map below, in a runner group restricted to `COG-GTM/opensearch-build`. macOS pools keep their dedicated hosts allocated (EC2 Mac hosts have a 24 h minimum) and re-launch the instance from the AMI after each job.
4. **Self-hosted runners never run untrusted code.** No workflow on a self-hosted label triggers on `pull_request` / `pull_request_target` from forks; ports use `workflow_dispatch`, `schedule`, `workflow_call`, `repository_dispatch` and `push` to protected branches. The existing PR-checking workflows (`python-tests.yml`, `groovy-tests.yml`, `workflow-lint.yml`, …) stay on GitHub-hosted `ubuntu-latest`.
5. **Instance profile grants nothing a job needs.** The runner's instance profile only allows what the runner itself needs to boot (SSM / CloudWatch agent, ECR pull for the CI images). Every AWS call a job makes goes through the OIDC roles in section 2, so a job only gets the role its workflow asks for.
6. **Timeouts and concurrency are explicit.** Every job sets `timeout-minutes` from the Jenkins `timeout(...)` option (e.g. `distribution-build` 240, `benchmark-test` 1440). Self-hosted jobs are capped at 5 days (7200 min), so `benchmark-test-endpoint` (10 days in Jenkins) cannot keep its timeout: s4.5 caps it at 7200 min or splits the run into resumable jobs. Every workflow that had `disableConcurrentBuilds` gets a `concurrency:` group. `skipIfLocked` (`check-for-build`) is different: a `concurrency:` group queues the duplicate instead of dropping it, so that port sets `run-name: check-for-build ${{ inputs.manifest }} ${{ inputs.target }}` (scheduled runs dispatch themselves with explicit inputs so the key is always set) and starts with a guard job that lists the `queued` / `in_progress` runs of the workflow (`gh run list --json databaseId,displayTitle,status`) whose `displayTitle` equals its own and sets output `proceed=true` only if its own run ID is the lowest of them. Lowest-ID-wins elects exactly one run when two start together and never preempts a run that is already building; every downstream build and SHA-upload job has `if: needs.guard.outputs.proceed == 'true'`, because a successful guard alone still satisfies `needs`.

#### Label map

`runs-on` is always the full label array so a job can only land on its own pool. `self-hosted` plus OS and architecture are the runner's default labels; the last label names the pool and is unique.

| Jenkins label (`Jenkins-Agent-` prefix dropped) | Files (non-legacy) | `runs-on` | Instance / AMI | Build environment |
| --- | ---: | --- | --- | --- |
| `AL2023-X64-M54xlarge-Docker-Host` | 25 | `[self-hosted, linux, x64, al2023-x64-m5-4xlarge-docker-host]` | m5.4xlarge, AL2023 x64 agent AMI | job `container:` = manifest `ci.image` (Linux build/test images) |
| `AL2023-Arm64-M6g4xlarge-Docker-Host` | 8 | `[self-hosted, linux, arm64, al2023-arm64-m6g-4xlarge-docker-host]` | m6g.4xlarge, AL2023 arm64 agent AMI | job `container:` = manifest `ci.image` (multi-arch images) |
| `Windows2019-X64-M54xlarge-Docker-Host` | 7 | `[self-hosted, windows, x64, windows2019-x64-m5-4xlarge-docker-host]` | m5.4xlarge, Windows 2019 agent AMI | `ci-runner-windows2019-opensearch-build-v1` started with `docker run -u ContainerAdministrator` (see Containers) |
| `Windows2019-X64-M54xlarge-Docker-Builder` | 1 | `[self-hosted, windows, x64, windows2019-x64-m5-4xlarge-docker-builder]` | m5.4xlarge, Windows 2019 agent AMI | `docker run` with `-v //./pipe/docker_engine://./pipe/docker_engine`, as `docker-build-lf` does today |
| `Ubuntu2404-X64-M52xlarge-Docker-Builder` | 4 | `[self-hosted, linux, x64, ubuntu2404-x64-m5-2xlarge-docker-builder]` | m5.2xlarge, Ubuntu 24.04 builder AMI | job `container:` = `ci-runner:ubuntu2404-x64-docker-buildx0.9.1-qemu8.2-v1` with the Docker socket mounted (buildx + qemu multi-arch builds) |
| `AL2023-X64-C54xlarge-Single-Host` | 1 | `[self-hosted, linux, x64, al2023-x64-c5-4xlarge-single-host]` | c5.4xlarge, AL2023 x64 agent AMI | systemd test images (`ci-runner-almalinux8-systemd-base-integtest-v1`, `ci-runner-ubuntu2404-systemd-base-integtest-v1`) run with `docker run -d --privileged --cgroupns=host --entrypoint=/usr/lib/systemd/systemd` and driven by `docker exec` |
| `AL2023-Arm64-C6g4xlarge-Single-Host` | 1 | `[self-hosted, linux, arm64, al2023-arm64-c6g-4xlarge-single-host]` | c6g.4xlarge, AL2023 arm64 agent AMI | same as the x64 single host |
| `AL2023-X64-M52xlarge-Benchmark-Test` | 5 | `[self-hosted, linux, x64, al2023-x64-m5-2xlarge-benchmark-test]` | m5.2xlarge, AL2023 x64 agent AMI, benchmark VPC | on the host, as today (`runBenchmarkTestScript` drives CDK + opensearch-benchmark from the agent) |
| `AL2023-X64-M52xlarge-Vector-Benchmark-Test` | 1 | `[self-hosted, linux, x64, al2023-x64-m5-2xlarge-vector-benchmark-test]` | m5.2xlarge, AL2023 x64 agent AMI, benchmark VPC | on the host, as today |
| `MacOS14-X64-Mac1-Multi-Host` | 1 | `[self-hosted, macos, x64, macos14-x64-mac1]` | mac1.metal dedicated host, macOS 14 agent AMI | on the host, as today (no container on macOS) |
| `MacOS14-ARM64-Mac2-Multi-Host` | 1 | `[self-hosted, macos, arm64, macos14-arm64-mac2]` | mac2.metal dedicated host, macOS 14 agent AMI | on the host, as today |

Labels used only by retired `jenkins/legacy/` files (`Ubuntu2404-X64-M58xlarge-Single-Host`, `Windows2019-X64-C524xlarge-Single-Host`, both from `legacy/gradle/gradle-check.jenkinsfile`) get no pool.

Pipelines that take the label as the `AGENT_LABEL` parameter (`bwc-test`, the test jobs launched by the distribution builds) take a `runner` input holding the JSON array from this table and use `runs-on: ${{ fromJSON(inputs.runner) }}`; the reusable per-platform workflow (s2.2) maps `platform`/`architecture` to these arrays in one place.

#### Containers

* The `docker/ci` CI-runner images stay the build environment. A Linux job resolves the image and args from the manifest's `ci.image` / `ci.images.<platform>.<distribution>` exactly as `detectDockerAgent` / `detectTestDockerAgent` do (s2.1 composite action), returns them as job outputs and runs the build job with `container: { image: ..., options: ... }`.
* Images are pulled through the same mirror Jenkins uses, `public.ecr.aws/opensearchorg/opensearchstaging/ci-runner:<tag>`, not Docker Hub directly.
* GitHub Actions does not support `container:` on Windows or macOS runners, nor `--entrypoint` in `container.options`. Windows jobs and the systemd single-host tests therefore start the image with `docker run` and run each step with `docker exec`; s2.1 ships this as one composite action so ports do not hand-roll it.
* zizmor's `unpinned-images` audit (`.github/zizmor.yml`) fires on runtime-resolved images. Either pin the image by digest in the manifest or add a reviewed ignore entry naming the workflow, as `python-tests.yml` already has.

### 2. Secrets

#### AWS: OIDC, no stored keys

Every AWS call uses `aws-actions/configure-aws-credentials` (pinned by SHA) with `role-to-assume`, `role-session-name: gha-${{ github.run_id }}-${{ github.run_attempt }}` and `audience: sts.amazonaws.com`; the job declares `permissions: id-token: write` and nothing broader than it needs. Each Jenkins `withAWS(role:, roleAccount:)` becomes one IAM role trusted by the GitHub OIDC provider in its account; the account-number secrets disappear into the role ARN. Trust policies pin `token.actions.githubusercontent.com:sub`:

* roles marked **release** below: `repo:COG-GTM/opensearch-build:environment:release` only;
* roles marked **benchmark**: `repo:COG-GTM/opensearch-build:environment:benchmark` only;
* all others: `repo:COG-GTM/opensearch-build:ref:refs/heads/main` and `repo:COG-GTM/opensearch-build:environment:ci`.

| Jenkins role (`withAWS role` @ account secret) | GHA secret (role ARN) | Trust | Session | Used by |
| --- | --- | --- | --- | --- |
| `OpenSearchJenkinsAccessRole` @ `aws-accounts/jenkins-health-metrics-account-number` | `AWS_ROLE_METRICS` | main / ci | 900 s (1800 s where Jenkins used it) | distribution builds, integ/smoke tests, `integ-test-notification`, `gradle-check-flaky-test-detector`, `release-chores`, `release-schedule` |
| `opensearch-test` @ `aws-accounts/jenkins-aws-account-public` | `AWS_ROLE_TEST_RESULTS` | main / ci, benchmark | 900 s | bwc/integ/smoke tests, `benchmark-test` (test-result upload) |
| `opensearch-bundle` @ `aws-accounts/jenkins-aws-account-public` | `AWS_ROLE_ARTIFACTS` | main / ci, release | 900 s | `promote-artifacts`, `sign-standalone-artifacts` (`uploadToS3`), artifact reads (`downloadFromS3`) |
| `jenkins-artifact-promotion-role` (`aws-iam-roles/`) @ `aws-accounts/jenkins-aws-production-account` | `AWS_ROLE_PROMOTION` | release | 900 s | `promote-artifacts`, `promote-repos`, `promote-docker-ecr` / production `docker-copy` (ECR Public login), `publish-min-snapshots`, `release-promotion` (CloudFront invalidation) |
| `jenkins-prod-rpm-signing-assume-role` @ `rpm-signing/jenkins-rpm-signing-account-number` | `AWS_ROLE_RPM_SIGNING` | release | 900 s | `promote-repos`, `sign-standalone-artifacts` (RPM) |
| `opensearch-full-access-nightlies` @ `aws-accounts/perf-test-account-id` | `AWS_ROLE_BENCHMARK` | benchmark | 43200 s (role `MaxSessionDuration` 12 h, the IAM maximum); benchmark jobs run up to 24 h, so each phase that calls AWS re-runs `configure-aws-credentials` instead of reusing the first session | `benchmark-test`, `benchmark-test-endpoint`, `benchmark-pull-request`, `benchmark-compare` |
| `opensearch-packer` @ `aws-accounts/jenkins-aws-account-public-lf` | `AWS_ROLE_PACKER` | main / ci | 3600 s | `packer-build` |
| agent instance profile (signer client base identity) | `AWS_ROLE_SIGNING_BASE` | release | 900 s | `sign-standalone-artifacts`, `promote-repos`; the `SIGNER_*_ROLE` trust policies must allow this role |
| agent instance profile (Bedrock) | `BEDROCK_ACCESS_ROLE` (exists, reused) | main / ci | 900 s | `release-notes-generate` |

#### GitHub writes: GitHub App token

`op:github-bot/ci-bot-token`, `op:github-bot/ci-bot-username` and `jenkins-github-bot-token` are replaced by a GitHub App installed on `COG-GTM/opensearch-build` and on the component repositories the release jobs write to. Jobs mint a short-lived installation token with `actions/create-github-app-token` (pinned by SHA), passing `owner:` / `repositories:` and the narrowest `permission-*` inputs the step needs (`contents: write` for branches/tags/commits, `pull-requests: write` for manifest and release-notes PRs, `issues: write` for build-failure and release issues). The token is passed to `gh` / scripts as `GITHUB_TOKEN` and never persisted (`actions/checkout` keeps `persist-credentials: false`); commits are authored as `<app-slug>[bot]`. Jobs that only touch this repository and need no downstream workflow triggers use the built-in `GITHUB_TOKEN` with explicit `permissions:` instead. The existing `OPENSEARCH_CI_BOT_TOKEN` used by today's workflows is out of scope for this migration.

GenericTrigger webhook tokens (`jenkins-pr-benchmark-generic-webhook-token`, and the retired `jenkins-gradle-check-generic-webhook-token`) get no GHA secret: callers switch to `repository_dispatch` / `workflow_dispatch`, which GitHub authenticates. Re-pointing the callers is s6.2.

#### Environments

| Environment | Protection | Deployment branches / tags | Holds |
| --- | --- | --- | --- |
| `ci` | none | `main` | staging Docker Hub, webhook, packer IDs |
| `benchmark` | none | `main` | benchmark datastore, webhook |
| `release` | required reviewers (release managers), prevent self-review | `main`, `refs/tags/*` | production Docker Hub, Maven Central, every signing secret, CloudFront, production bucket |

Repository-level secrets hold only the non-release role ARNs, the GitHub App credentials and the shared bucket/endpoint names. Promotion jobs (`promote-artifacts`, `promote-repos`, `promote-docker-ecr`, `publish-to-maven`, `sign-standalone-artifacts`, `release-promotion`, and `docker-copy` / `publish-min-snapshots` when the destination is production) declare `environment: release` on the job that holds the secret, so a reviewer approves each promotion run. `docker-copy.yml` picks `ci` or `release` from its destination-registry input.

#### Secret list for p2

Names only; values are copied from the 1Password item in the **Replaces** column, except role ARNs (from the roles s2.4 creates) and `GH_APP_ID` / `GH_APP_PRIVATE_KEY` (from the new GitHub App s2.4 registers; the bot username/token they replace cannot mint installation tokens). Every name below is referenced as `${{ secrets.NAME }}`; no value is ever committed.

| Secret | Scope | Replaces (Jenkins / `op://opensearch-release-secrets/…`) | Used by |
| --- | --- | --- | --- |
| `GH_APP_ID` | repo | `github-bot/ci-bot-username` (new value: the App's ID) | every job that writes to GitHub beyond `GITHUB_TOKEN` |
| `GH_APP_PRIVATE_KEY` | repo | `github-bot/ci-bot-token`, `jenkins-github-bot-token` (new value: the App's private key) | same |
| `AWS_ROLE_METRICS` | repo | `OpenSearchJenkinsAccessRole` + `aws-accounts/jenkins-health-metrics-account-number` | see role table |
| `AWS_ROLE_TEST_RESULTS` | repo | `opensearch-test` + `aws-accounts/jenkins-aws-account-public` | see role table |
| `AWS_ROLE_ARTIFACTS` | repo | `opensearch-bundle` + `aws-accounts/jenkins-aws-account-public` | see role table |
| `AWS_ROLE_PACKER` | repo | `opensearch-packer` + `aws-accounts/jenkins-aws-account-public-lf` | `packer-build` |
| `BEDROCK_ACCESS_ROLE` | repo (exists) | agent instance profile | `release-notes-generate` |
| `METRICS_CLUSTER_ENDPOINT` | repo | `metrics-cluster/jenkins-health-metrics-cluster-endpoint` | distribution builds, integ/smoke tests, `integ-test-notification`, `gradle-check-flaky-test-detector`, `release-chores`, `release-schedule` |
| `ARTIFACT_BUCKET_NAME` | repo | `aws-resource-arns/jenkins-artifact-bucket-name` | distribution builds, tests, `check-for-build`, `feature-build-opensearch`, benchmarks, `promote-artifacts`, `sign-standalone-artifacts` |
| `ARTIFACT_BUCKET_NAME_LF` | repo | `aws-resource-arns/jenkins-artifact-bucket-name-lf` | `publish-to-maven` (until the LF and non-LF buckets are merged) |
| `INTEG_TEST_WEBHOOK_URL` | env `ci`, `benchmark` | `jenkins-integ-test-webhook` (`webhook/jenkins-integ-test-webhook`) | Dashboards `bwc-test`, `benchmark-test` (incl. vectorsearch and datafusion variants) |
| `DOCKERHUB_STAGING_USERNAME` | env `ci` | `dockerhub-staging-credentials/username` | `docker-build`, staging `docker-copy` |
| `DOCKERHUB_STAGING_PASSWORD` | env `ci` | `dockerhub-staging-credentials/password` | same |
| `DOCKERHUB_READONLY_USERNAME` | env `ci`, `release` | `dockerhub-production-readonly-credentials/username` | `docker-copy` (source pulls) |
| `DOCKERHUB_READONLY_PASSWORD` | env `ci`, `release` | `dockerhub-production-readonly-credentials/password` | same |
| `PACKER_BUILD_VPC_ID` | env `ci` | `packer-build-ids/jenkins-agent-vpc-id-lf` | `packer-build` |
| `PACKER_BUILD_SUBNET_ID` | env `ci` | `packer-build-ids/jenkins-agent-subnet-id-lf` | `packer-build` |
| `PACKER_BUILD_SG_ID` | env `ci` | `packer-build-ids/jenkins-agent-sg-id-lf` | `packer-build` |
| `PACKER_BUILD_REGION` | env `ci` | `packer-build-ids/jenkins-agent-region-id-lf` | `packer-build` |
| `AWS_ROLE_BENCHMARK` | env `benchmark` | `opensearch-full-access-nightlies` + `aws-accounts/perf-test-account-id` | benchmarks |
| `BENCHMARK_METRICS_DATASTORE_USER` | env `benchmark` | `benchmark-metrics/benchmark-metrics-datastore-user` | benchmarks |
| `BENCHMARK_METRICS_DATASTORE_PASSWORD` | env `benchmark` | `benchmark-metrics/benchmark-metrics-datastore-password` | benchmarks |
| `BENCHMARK_METRICS_DATASTORE_NLB_ENDPOINT` | env `benchmark` | `benchmark-metrics/benchmark-metrics-datastore-nlb-endpoint` | `benchmark-pull-request` |
| `AWS_ROLE_PROMOTION` | env `release` | `aws-iam-roles/jenkins-artifact-promotion-role` + `aws-accounts/jenkins-aws-production-account` | see role table |
| `AWS_ROLE_RPM_SIGNING` | env `release` | `jenkins-prod-rpm-signing-assume-role` + `rpm-signing/jenkins-rpm-signing-account-number` | `promote-repos`, `sign-standalone-artifacts` |
| `AWS_ROLE_SIGNING_BASE` | env `release` | agent instance profile | `promote-repos`, `sign-standalone-artifacts` |
| `ARTIFACT_PRODUCTION_BUCKET_NAME` | env `release` | `aws-resource-arns/jenkins-artifact-production-bucket-name` | `promote-artifacts`, `promote-repos`, `publish-min-snapshots` |
| `CLOUDFRONT_DISTRIBUTION_ID` | env `release` | `aws-resource-arns/cloudfront-distribution-id` | `release-promotion` |
| `DOCKERHUB_PRODUCTION_USERNAME` | env `release` | `dockerhub-production-credentials/username` | production `docker-copy`, `promote-docker-ecr` |
| `DOCKERHUB_PRODUCTION_PASSWORD` | env `release` | `dockerhub-production-credentials/password` | same |
| `MAVEN_CENTRAL_PORTAL_USERNAME` | env `release` | `maven-central-portal-credentials/username` | `publish-to-maven` |
| `MAVEN_CENTRAL_PORTAL_PASSWORD` | env `release` | `maven-central-portal-credentials/password` | `publish-to-maven` |
| `RPM_SIGNING_KEY_ID` | env `release` | `rpm-signing/jenkins-rpm-signing-key-id` | `promote-repos`, `sign-standalone-artifacts` |
| `RPM_SIGNING_PASSPHRASE_SECRETS_ARN` | env `release` | `rpm-signing/jenkins-rpm-signing-passphrase-secrets-arn` | same |
| `RPM_SIGNING_SECRET_KEY_ID_SECRETS_ARN` | env `release` | `rpm-signing/jenkins-rpm-signing-secret-key-secrets-arn` | same |
| `RPM_RELEASE_SIGNING_KEY_ID` | env `release` | `rpm-signing/jenkins-rpm-release-signing-key-id` | same |
| `RPM_RELEASE_SIGNING_PASSPHRASE_SECRETS_ARN` | env `release` | `rpm-signing/jenkins-rpm-release-signing-passphrase-secrets-arn` | same |
| `RPM_RELEASE_SIGNING_SECRET_KEY_ID_SECRETS_ARN` | env `release` | `rpm-signing/jenkins-rpm-release-signing-secret-key-secrets-arn` | same |
| `SIGNER_CLIENT_ROLE` | env `release` | `client-signing/jenkins-signer-client-role` | `sign-standalone-artifacts` (PGP) |
| `SIGNER_CLIENT_EXTERNAL_ID` | env `release` | `client-signing/jenkins-signer-client-external-id` | same |
| `SIGNER_CLIENT_UNSIGNED_BUCKET` | env `release` | `client-signing/jenkins-signer-client-unsigned-bucket` | same |
| `SIGNER_CLIENT_SIGNED_BUCKET` | env `release` | `client-signing/jenkins-signer-client-signed-bucket` | same |
| `JAR_SIGNER_ROLE` | env `release` | `jar-signing/jenkins-jar-signer-role` | `sign-standalone-artifacts` (jar) |
| `JAR_SIGNER_EXTERNAL_ID` | env `release` | `jar-signing/jenkins-jar-signer-external-id` | same |
| `JAR_SIGNER_UNSIGNED_BUCKET` | env `release` | `jar-signing/jenkins-jar-signer-unsigned-bucket` | same |
| `JAR_SIGNER_SIGNED_BUCKET` | env `release` | `jar-signing/jenkins-jar-signer-signed-bucket` | same |
| `SIGNER_MAC_ROLE` | env `release` | `mac-signing/jenkins-signer-mac-role` | `sign-standalone-artifacts` (mac) |
| `SIGNER_MAC_EXTERNAL_ID` | env `release` | `mac-signing/jenkins-signer-mac-external-id` | same |
| `SIGNER_MAC_UNSIGNED_BUCKET` | env `release` | `mac-signing/jenkins-signer-mac-unsigned-bucket` | same |
| `SIGNER_MAC_SIGNED_BUCKET` | env `release` | `mac-signing/jenkins-signer-mac-signed-bucket` | same |
| `SIGNER_WINDOWS_ROLE` | env `release` | `windows-signing/jenkins-signer-windows-role` | `sign-standalone-artifacts` (windows) |
| `SIGNER_WINDOWS_EXTERNAL_ID` | env `release` | `windows-signing/jenkins-signer-windows-external-id` | same |
| `SIGNER_WINDOWS_UNSIGNED_BUCKET` | env `release` | `windows-signing/jenkins-signer-windows-unsigned-bucket` | same |
| `SIGNER_WINDOWS_SIGNED_BUCKET` | env `release` | `windows-signing/jenkins-signer-windows-signed-bucket` | same |
| `SIGNER_WINDOWS_PROFILE_IDENTIFIER` | env `release` | `windows-signing/jenkins-signer-windows-profile-identifier` | same |
| `SIGNER_WINDOWS_PLATFORM_IDENTIFIER` | env `release` | `windows-signing/jenkins-signer-windows-platform-identifier` | same |

Environment variable names inside steps keep the names the scripts already read (`SIGNER_WINDOWS_ROLE`, `RPM_SIGNING_KEY_ID`, `DOCKER_USERNAME`, `GITHUB_TOKEN`, `WEBHOOK_URL`, …), so `src/` and `scripts/` do not change.

Jenkins credentials with no GHA secret: the account-number entries (`aws-accounts/*`, `rpm-signing/jenkins-rpm-signing-account-number`; folded into role ARNs), `aws-iam-roles/jenkins-artifact-promotion-role` (folded into `AWS_ROLE_PROMOTION`), the GenericTrigger tokens (replaced by GitHub-authenticated dispatch), and everything referenced only by retired `jenkins/legacy/` files (`gradle-check/*`, `jenkins-sonatype-creds`, `maven-snapshots-*`, `mend-scan/wss-token`, `jenkins-data-prepper-*`).

`benchmark-test-endpoint` takes the target cluster's `USERNAME` / `PASSWORD` as Jenkins parameters. In GHA the password must not be a `workflow_dispatch` input (inputs are shown in the run UI); scheduled configs reference a `benchmark`-environment secret named in the schedule config, and s4.5 lists those names when it ports the schedules.

## Consequences

* s2.4 provisions: the runner pools in the label map, the OIDC provider and the roles in the role table (with the trust conditions above), the GitHub App, the three environments and every secret in the list.
* s2.1 / s2.2 ship the manifest-to-container resolver, the `docker run` / `docker exec` composite for Windows and systemd hosts, and the platform-to-`runs-on` map; ports call those instead of repeating them.
* Ports reference secrets only by the names above. Adding a new secret or role means amending this ADR in the same PR.
* Jenkins keeps its credentials until s6.2 disables the jobs; nothing under `jenkins/` changes here.

## Alternatives considered

* **Stored AWS keys as repository secrets.** Rejected: long-lived keys, no per-workflow scoping, and rotation has to be manual. OIDC trust policies scope each role to a branch or environment.
* **Keep the 1Password references and read them at job time (`1password/load-secrets-action`).** Rejected for this migration: it still needs one long-lived service-account token in GitHub, and environment reviewers cannot gate individual secrets inside a vault. It can replace the copied values later without changing the secret names here.
* **Long-lived (non-ephemeral) self-hosted runners.** Ruled out by `d-runners`: state leaks between jobs, which Jenkins only mitigated with `postCleanup`.
* **PAT for the bot account instead of a GitHub App.** Rejected: tied to a user, broad scopes, no expiry by default. App tokens expire after an hour and are scoped per call.
