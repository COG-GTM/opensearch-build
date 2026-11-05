# GitHub Actions runners and AWS OIDC roles

Terraform for plan step s2.4 of the Jenkins → GitHub Actions migration. It implements the runner and
AWS-access decisions of [ADR 0001](../../docs/adr/0001-gha-runners-and-secrets.md) (`d-runners`:
ephemeral self-hosted runners on AWS from the Jenkins AMIs).

The Jenkins agents themselves are managed by CDK in
[opensearch-ci](https://github.com/opensearch-project/opensearch-ci); nothing here changes them, and
every role name is new (`gha-opensearch-build-*`), so Jenkins keeps working until cutover.

## What it creates

| File | Resources |
| --- | --- |
| `runner-pools.yaml` | One pool per row of the ADR label map: `runs-on` labels, instance type, max size. Checked against the ADR by `tests/tests_deployment/test_gha_runner_pools.py`. |
| `runners.tf` | [`github-aws-runners/github-runner/aws//modules/multi-runner`](https://registry.terraform.io/modules/github-aws-runners/github-runner/aws/latest) `8.1.0`: webhook (API Gateway + Lambda → EventBridge, `workflow_job` events only), one scale-up/scale-down queue per pool, launch templates, instance profiles. EC2 Mac dedicated hosts, host resource groups and the license configuration for the two macOS pools. |
| `oidc.tf`, `modules/github-oidc-roles` | The `token.actions.githubusercontent.com` IAM OIDC provider and one role per entry of the ADR role table, in the account that owns the resource it touches. |
| `github.tf` | Org runner group `opensearch-build` (visible to this repository only), environments `ci` / `benchmark` / `release` with their deployment policies and release reviewers, and the `AWS_ROLE_*` secrets holding the role ARNs. Skipped with `manage_github = false`. |

### Runner behaviour

* **Ephemeral, one job per instance.** `enable_ephemeral_runners` + JIT config: an instance registers
  for one job and is terminated afterwards; nothing (workspace, Docker cache, credentials) survives.
* **Scale from zero.** No warm pool or idle config. A queued `workflow_job` whose label set equals a
  pool's (`exactMatch` + `bidirectionalLabelMatch`) starts one instance, up to the pool's `max_runners`.
* **Repository-scoped.** Org runners in the `opensearch-build` runner group (selected repository only),
  and the webhook drops events from any other repository (`repository_white_list`). Fork PRs never
  reach these runners: PR checks stay on GitHub-hosted runners (ADR 0001).
* **Instance profile is boot-time only:** SSM (JIT config, Session Manager), CloudWatch agent, ECR
  read-only. Workflows get AWS access only through the OIDC roles below.
* On-demand capacity (no spot interruptions mid-build), IMDSv2 required with hop limit 1 (job containers
  cannot reach the instance profile), encrypted gp3 root volumes. The Ubuntu pool boots with
  `templates/user-data-ubuntu.sh` (the module default assumes AL2023).
  Benchmark pools use `benchmark_vpc_id` / `benchmark_subnet_ids` when set.

### OIDC roles

Every trust policy pins `aud = sts.amazonaws.com` and an exact `sub` list (no wildcards; enforced by a
precondition):

| Secret (scope) | Role | Account key | Trusted `sub` | Permissions |
| --- | --- | --- | --- | --- |
| `AWS_ROLE_METRICS` (repo) | `gha-opensearch-build-metrics` | `metrics` | main, `ci` | `es:ESHttp*` on the metrics domain |
| `AWS_ROLE_ARTIFACTS` (repo) | `gha-opensearch-build-artifacts` | `public` | main, `ci`, `release` | read/write the artifacts bucket |
| `AWS_ROLE_TEST_RESULTS` (repo) | `gha-opensearch-build-test-results` | `public` | main, `ci`, `benchmark` | read bundles, write `*/test-results/*` |
| `AWS_ROLE_PACKER` (repo) | `gha-opensearch-build-packer` | `public_lf` | main, `ci` | Packer `amazon-ebs` EC2 set |
| `AWS_ROLE_BENCHMARK` (env `benchmark`) | `gha-opensearch-build-benchmark` | `perf` | `benchmark` | assume the perf account CDK bootstrap roles; 12 h max session |
| `AWS_ROLE_PROMOTION` (env `release`) | `gha-opensearch-build-release-promotion` | `production` | `release` | production bucket, CloudFront invalidation, ECR Public push |
| `AWS_ROLE_RPM_SIGNING` (env `release`) | `gha-opensearch-build-rpm-signing` | `rpm_signing` | `release` | read the RPM signing secrets |
| `AWS_ROLE_SIGNING_BASE` (env `release`) | `gha-opensearch-build-signing-base` | `runner` | `release` | assume the signer roles |

"main" is `repo:COG-GTM/opensearch-build:ref:refs/heads/main`; an environment name is
`repo:COG-GTM/opensearch-build:environment:<name>`. The read/build roles never trust `release`-only
work and the release roles trust nothing but the `release` environment, which requires a reviewer.

## Prerequisites (one-time, outside Terraform)

1. **GitHub App** for the runner control plane, installed on `COG-GTM` with *Organization → Self-hosted
   runners: read & write*, *Repository → Actions: read*, *Metadata: read*, and the `workflow_job` event.
   Store its ID, base64 private key and a webhook secret as SSM `SecureString` parameters in the runner
   account under the names in `var.github_app` (defaults `/gha-runners/opensearch-build/github-app/*`).
   This is not the release-bot App behind `GH_APP_ID` / `GH_APP_PRIVATE_KEY`.
2. **AMI parameters**: SSM `String` parameters (data type `aws:ec2:image`) named as in
   `var.runner_ami_ssm_parameters`, holding the Jenkins agent AMI IDs. The packer workflow (s3.5) keeps
   them current; the plan fails early if one is missing.
3. **State backend**: an S3 bucket + DynamoDB lock table in the runner account.
4. **Cross-account access**: a role Terraform can assume in each account of `var.account_role_arns`,
   and, if the GitHub OIDC provider already exists in an account, `create_oidc_provider.<key> = false`.
   Also set it `false` for a key that points at an account another key already covers.
5. **Bucket / signer trust** owned elsewhere: the signer roles' trust policies must allow
   `AWS_ROLE_SIGNING_BASE`, and the artifacts bucket policy must allow cross-account reads by the
   promotion role if the release job reads bundles with it.
6. **EC2 Mac capacity**: each macOS pool gets one dedicated host in `mac_dedicated_host_azs[<pool>]`
   (default `<aws_region>a`). `subnet_ids` must include a subnet in that AZ that offers `mac1.metal` /
   `mac2.metal`; otherwise set the AZs explicitly.
7. **GitHub token** with org admin (runner group) and repo admin (environments, secrets) in `GITHUB_TOKEN`.

## Validate and plan

```bash
cd deployment/gha-runners
scripts/download-lambdas.sh            # lambda zips for the pinned module version
terraform init -backend=false && terraform validate
```

Sandbox dry run (AWS public AMIs, placeholder ARNs, local throwaway state, GitHub side skipped):

```bash
AWS_REGION=us-east-1 scripts/plan-sandbox.sh   # writes out/validate.txt and out/plan.txt
```

The sandbox plan only reads (`describe-*`, SSM public parameters); never apply it.

## Apply procedure (human-approved, not part of s2.4)

Applying creates IAM roles in shared accounts and starts accepting webhook traffic, so it is a separate
action approved on the ticket by a maintainer.

1. Complete the prerequisites and write `prod.tfvars` (not committed) with `vpc_id`, `subnet_ids`,
   `benchmark_*`, `account_role_arns`, `create_oidc_provider`, the resource ARNs/names,
   `release_reviewers`, and any `runner_max_count_overrides`.
2. Initialise against the shared state:
   ```bash
   terraform init -reconfigure -backend-config="bucket=<state-bucket>" -backend-config="key=gha-runners/opensearch-build.tfstate" \
     -backend-config="region=us-east-1" -backend-config="dynamodb_table=<lock-table>"
   ```
3. `terraform plan -var-file=prod.tfvars -out=prod.tfplan`, attach the plan output to the ticket, and get
   approval for that exact plan.
4. `terraform apply prod.tfplan`.
5. Set the GitHub App webhook URL to `terraform output -raw webhook_endpoint` and its secret to the SSM
   webhook secret.
6. Smoke test: a `workflow_dispatch` job on each pool's `runs-on` array starts one instance, runs, and the
   instance is gone within minutes; a job assuming each `AWS_ROLE_*` from the right ref/environment
   succeeds and from the wrong one is denied.
7. Record the apply (plan, apply output, smoke-test run links) on the ticket.

Rollback: set a pool's `runner_max_count_overrides` to `0` to stop new instances, or
`terraform destroy -target=module.runners` for the runners; the OIDC roles can stay without effect while
no workflow references them.
