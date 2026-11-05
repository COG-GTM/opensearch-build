# Packer AMI build (`packer-build.yml`)

GitHub Actions port of `jenkins/packer/packer-build-lf.jenkinsfile`. It builds the agent AMIs from the
[opensearch-ci packer templates](https://github.com/opensearch-project/opensearch-ci/tree/main/packer); per
[ADR 0001](../../docs/adr/0001-gha-runners-and-secrets.md) the same AMIs back the ephemeral self-hosted runner
pools in [`deployment/gha-runners`](../gha-runners).

| Jenkins | GitHub Actions |
| --- | --- |
| `PACKER_TEMPLATE_NAME` parameter | `PACKER_TEMPLATE_NAME` `workflow_dispatch` input (also `legacy/<name>.json`) |
| `withSecrets` `packer-build-ids/jenkins-agent-{vpc,subnet,sg,region}-id-lf` | `ci` environment secrets `PACKER_BUILD_VPC_ID`, `PACKER_BUILD_SUBNET_ID`, `PACKER_BUILD_SG_ID`, `PACKER_BUILD_REGION` |
| `withAWS(role: 'opensearch-packer', roleAccount: AWS_ACCOUNT_PUBLIC, duration: 3600)` | OIDC role `AWS_ROLE_PACKER` (`deployment/gha-runners/oidc.tf`), 3600 s |
| `readJSON` / `writeJSON` → `substitute_<template>` | [`render_template.py`](render_template.py) → `substitute_<template>` |
| `packer build -color=false` | same, after `packer validate` + `packer inspect` |

## What `render_template.py` adds

* The Jenkins variable substitution (`build-vpc`, `build-subnet`, `build-secgrp`, `build-region`, `aws_ami_region`).
* With `INSTALL_GHA_RUNNER` (default on): the GitHub Actions runner agent, pinned to `RUNNER_VERSION` and its
  release SHA-256, unpacked to `/opt/actions-runner` (Linux/macOS, [`install-gha-runner.sh`](gha-runner/install-gha-runner.sh))
  or `C:\actions-runner` (Windows, [`install-gha-runner.ps1`](gha-runner/install-gha-runner.ps1)), inserted before the
  template's final reboot / sysprep step. Nothing is registered: the runner module's boot script fetches a JIT config.
  The Jenkins setup is untouched, so the AMIs still work as Jenkins agents until cutover.

  With the current `deployment/gha-runners` settings (module user data on, runner binaries syncer on), the
  module's boot-time `install_runner` extracts the syncer's runner over `/opt/actions-runner`, so jobs run that
  version and boot still downloads it; the baked copy only pre-installs the agent and its dependencies. Making the
  baked agent the one that runs (pool `enable_userdata = false`, `enable_runner_binaries_syncer = false`, and the
  module's `start-runner.sh` baked as a boot script, as in the module's prebuilt-AMI examples) is a change to the
  runner infrastructure, not to this workflow.
* A `manifest` post-processor so the run summary and the `packer-manifest` artifact list the AMI IDs.

To bump the agent, update `RUNNER_VERSION` and `RUNNER_SHA256` from the
[actions/runner release notes](https://github.com/actions/runner/releases).

## Runs

* **Pull requests / pushes to `main`** touching the workflow or this directory: `validate` renders every
  current template with placeholder network IDs and runs `packer validate` + `packer inspect`. No AWS access.
* **`workflow_dispatch`**: `validate` for the chosen template, then `packer-build` in the `ci` environment
  (skipped with `DRY_RUN`). Runs on GitHub-hosted `ubuntu-24.04`, since the self-hosted pools launch from these AMIs.

## After a build

The AMI is created in the `public_lf` account. The runner pools read AMI IDs from the SSM parameters in
`var.runner_ami_ssm_parameters` in the runner account, which `AWS_ROLE_PACKER` cannot write; until a role for
that exists, share the AMI with the runner account and update the parameter from the run summary, e.g.
`aws ssm put-parameter --name /gha-runners/opensearch-build/ami/al2023-x64 --type String --data-type aws:ec2:image --overwrite --value ami-...`.
