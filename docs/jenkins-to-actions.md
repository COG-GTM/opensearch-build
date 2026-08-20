# Jenkins to GitHub Actions migration: Docker and artifact validation

This section documents the additive migration of the Docker build/copy/scan/re-release and artifact-validation
pipelines. The Jenkins sources remain unchanged under `jenkins/` and `tests/jenkins/`.

## Docker build/copy/scan/re-release and validate-artifacts

### Files added

#### Workflows

| File | Trigger | Role |
| --- | --- | --- |
| `.github/workflows/docker-build.yml` | `workflow_dispatch`, `workflow_call` | Build and publish a Docker image to staging. |
| `.github/workflows/docker-copy.yml` | `workflow_dispatch`, `workflow_call` | Copy one tag or all `ci-runner` tags between supported registries. |
| `.github/workflows/docker-scan.yml` | `workflow_dispatch`, `workflow_call` | Scan an image with Trivy and upload table/JSON results. |
| `.github/workflows/docker-re-release.yml` | `workflow_dispatch` | Pull, patch, rebuild, copy, and scan a product image. |
| `.github/workflows/validate-artifacts.yml` | `workflow_dispatch` | Fan out artifact validation across distribution/architecture combinations. |

#### Composite actions

| Composite action | Shared-library source |
| --- | --- |
| `.github/actions/copy-container` | `vars/copyContainer.groovy` |
| `.github/actions/scan-docker-image` | `vars/scanDockerImage.groovy` |
| `.github/actions/patch-docker-image` | `vars/patchDockerImage.groovy` and `vars/buildDockerImage.groovy` |
| `.github/actions/validate-artifacts` | `vars/validateArtifacts.groovy` |

No composite action name or interface overlaps the actions present on
`origin/devin/1787177921-jenkins-to-actions`; that branch's action list was checked before implementation.

### Jenkins construct to GitHub Actions mapping

| Jenkins construct | Source | GitHub Actions equivalent |
| --- | --- | --- |
| Docker build parameters | `jenkins/docker/docker-build-lf.jenkinsfile:38-61` | `workflow_dispatch` and identical `workflow_call` inputs; `choice` becomes `string` for reusable calls. |
| Repository ABORTED guard | `jenkins/docker/docker-build-lf.jenkinsfile:63-71` | `parameters-check` job fails unless the repository starts with `https://github.com/opensearch-project/`. |
| Linux Docker build agent | `jenkins/docker/docker-build-lf.jenkinsfile:73-82` | `ubuntu-24.04` with the pinned public ECR ci-runner image and Docker socket mount. |
| Windows Docker build agent | `jenkins/docker/docker-build-lf.jenkinsfile:73-82` | `windows-2022` without a job container; GitHub Actions cannot mount the Windows Docker Engine pipe in a Windows job container. |
| Checkout, staging login, eval | `jenkins/docker/docker-build-lf.jenkinsfile:83-101` | Pinned checkout, `docker login --password-stdin`, and `eval` in the build job. |
| Docker build cleanup | `jenkins/docker/docker-build-lf.jenkinsfile:103-116` | `if: always()` logout/prune; `postCleanup`/`cleanWs` is unnecessary on ephemeral runners. |
| Copy parameters and description | `jenkins/docker/docker-copy-lf.jenkinsfile:23-50`, `:64-73` | Dispatch/reusable inputs, empty checks, and `$GITHUB_STEP_SUMMARY`. |
| Copy shared step | `jenkins/docker/docker-copy-lf.jenkinsfile:75-85` | `copy-container` composite action. |
| Copy post cleanup | `jenkins/docker/docker-copy-lf.jenkinsfile:89-95` | Always logout both registries and prune. |
| Scan parameters/environment | `jenkins/docker/docker-scan-lf.jenkinsfile:23-34` | Workflow input and Trivy environment block. |
| Scan shared step/archive | `jenkins/docker/docker-scan-lf.jenkinsfile:58-75` | `scan-docker-image`, then `actions/upload-artifact@v6` and always `trivy clean --all`. |
| Re-release schedule/parameters | `jenkins/docker/docker-re-release-lf.jenkinsfile:18-42` | Dispatch-only workflow; parameterizedCron is shown only as a commented equivalent. |
| Patch Docker image | `vars/patchDockerImage.groovy:15-73` | `patch-docker-image` pulls/inspects labels, reads the manifest, computes URLs/tags, and emits outputs. |
| Build Docker image | `vars/buildDockerImage.groovy:20-77` | Re-release calls reusable `docker-build.yml` with the exact assembled command string. |
| Build-number copy and scan | `vars/buildDockerImage.groovy:79-102` | Re-release chains reusable `docker-copy.yml` and `docker-scan.yml`. |
| Promotion downstream build | `vars/patchDockerImage.groovy:75-85` | Not invoked; a commented workflow skeleton records the future `docker-promotion` call. |
| Validation parameters | `jenkins/validate-artifacts/validate-artifacts-lf.jenkinsfile:50-126` | Nine main inputs plus `OPTIONS`, because GitHub Actions dispatch has a ten-input cap. |
| Validation normalization | `jenkins/validate-artifacts/validate-artifacts-lf.jenkinsfile:128-178` | `verify-parameters` expands Both values, derives URL distribution/architecture, builds `file-path`, and rejects missing inputs. |
| Parallel validation | `jenkins/validate-artifacts/validate-artifacts-lf.jenkinsfile:186-295` | JSON matrix with zip-arm64 skipped, hosted runner selection, and `container` disabled for Docker/zip. |
| Artifact validation shared step | `vars/validateArtifacts.groovy:29-58` | `validate-artifacts` assembles the same `validation.sh` flags. |
| `retry(2)` | `jenkins/validate-artifacts/validate-artifacts-lf.jenkinsfile:220-232`, `:251-271` | Two-attempt loop inside the composite action. |

### Shared-library steps

| Jenkins shared-library step | GitHub Actions port |
| --- | --- |
| `copyContainer` | `copy-container`; DockerHub branches, ECR OIDC/role chaining, crane guard, and logout are preserved. |
| `scanDockerImage` | `scan-docker-image`; pinned Trivy installation and the original result-file command sequence. |
| `patchDockerImage` + `buildDockerImage` | `patch-docker-image` plus `docker-re-release.yml` reusable-workflow chaining. |
| `postCleanup` | Not needed on ephemeral GitHub-hosted runners; Docker logout/prune remains where Jenkins had it. |
| `validateArtifacts` | `validate-artifacts`; command assembly and retry loop are kept in one composite action. |

### Required secrets and AWS configuration

The repository owner must configure these values; no values are invented in this migration:

* `DOCKERHUB_STAGING_USERNAME` / `DOCKERHUB_STAGING_PASSWORD`
* `DOCKERHUB_PRODUCTION_USERNAME` / `DOCKERHUB_PRODUCTION_PASSWORD`
* `DOCKERHUB_READONLY_USERNAME` / `DOCKERHUB_READONLY_PASSWORD`
* `AWS_ACCOUNT_ARTIFACT`
* `ARTIFACT_PROMOTION_ROLE_NAME`
* `ECR_STAGING_ROLE_ARN`

The Docker copy workflow grants `id-token: write` to the copy job. Configure the staging and artifact-promotion AWS
roles and their OIDC trust policies for this repository before dispatching the workflow. Staging ECR login assumes
`ECR_STAGING_ROLE_ARN`; production ECR login assumes
`arn:aws:iam::<AWS_ACCOUNT_ARTIFACT>:role/<ARTIFACT_PROMOTION_ROLE_NAME>`.

### Not migrated / needs a decision

* Jenkins `parameterizedCron` for the four monthly re-release combinations
  (`docker-re-release-lf.jenkinsfile:23-29`) is dispatch-only here.
* The `docker-promotion` downstream trigger (`patchDockerImage.groovy:75-85`) needs a future `gh workflow run
  docker-promotion.yml` dispatch or reusable `workflow_call`, with `SOURCE_IMAGES`, `RELEASE_VERSION`, and
  `TAG_LATEST` exactly as recorded in the commented skeleton.
* Windows Docker-in-Docker agents are not equivalent: the Docker build Windows branch and validate-artifacts zip
  branch cannot use the Jenkins Windows ci-runner container on GitHub-hosted runners.
* Jenkins agent labels map to `ubuntu-24.04`, `ubuntu-24.04-arm`, and `windows-2022` hosted runners here. The Jenkins
  fleet used large m5/c5 instances and real Docker hosts; capacity and Docker-in-Docker behavior need a runner decision.
* Jenkins `lock` and `buildDiscarder` are not applicable to these workflows.
* The tar validation container keeps Jenkins' `-u 1000` docker argument; on GitHub-hosted runners the
  checked-out workspace is owned by a different uid, so file permissions inside the tar leg may need a
  runner/uid decision.
* `retry(2)` is implemented inside the validation composite; job `timeout` values map to `timeout-minutes`.
* `currentBuild.description` maps to `$GITHUB_STEP_SUMMARY` for copy, scan, and validation parameter checks.
* `copyContainer.groovy:55-57` assumed ambient credentials for staging ECR. This port makes that assumption explicit by
  requiring an OIDC role ARN and configuring it inside the composite action.
* RPM/YUM/DEB systemd-entrypoint containers retain Jenkins' privileged/cgroup options verbatim. Systemd containers
  on GitHub-hosted runners are dubious and require runtime validation or a self-hosted runner decision.

### Validation

The workflows and composite actions are intended for `actionlint`, YAML parsing, and repository yamllint. They require
the OpenSearch build infrastructure, Docker registries, AWS roles, and artifact URLs for runtime validation.
