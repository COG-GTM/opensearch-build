# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

variable "aws_region" {
  description = "Region for the runner control plane, runners and the IAM provider calls."
  type        = string
  default     = "us-east-1"
}

variable "prefix" {
  description = "Name prefix for every AWS resource created by the runner module."
  type        = string
  default     = "osb-gha"
}

variable "tags" {
  description = "Extra tags applied to every AWS resource."
  type        = map(string)
  default     = {}
}

variable "account_role_arns" {
  description = <<-EOT
    Role Terraform assumes in each account of the ADR 0001 role table. null = use the caller's
    credentials (single-account sandbox). Accounts: runner (runners + signing-base role), metrics,
    public (test results + bundle), production (promotion), rpm_signing, perf (benchmark),
    public_lf (packer).
  EOT
  type = object({
    runner      = optional(string)
    metrics     = optional(string)
    public      = optional(string)
    production  = optional(string)
    rpm_signing = optional(string)
    perf        = optional(string)
    public_lf   = optional(string)
  })
  default = {}
}

variable "create_oidc_provider" {
  description = "Create the token.actions.githubusercontent.com IAM OIDC provider in each account. Set false where it already exists or where two keys point at the same account."
  type = object({
    runner      = optional(bool, true)
    metrics     = optional(bool, true)
    public      = optional(bool, true)
    production  = optional(bool, true)
    rpm_signing = optional(bool, true)
    perf        = optional(bool, true)
    public_lf   = optional(bool, true)
  })
  default = {}
}

# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

variable "github_owner" {
  description = "GitHub organization that owns the repository and the runner group."
  type        = string
  default     = "COG-GTM"
}

variable "github_repository" {
  description = "Repository name (without owner) the runners and OIDC roles are scoped to."
  type        = string
  default     = "opensearch-build"
}

variable "runner_group_name" {
  description = "Organization runner group every pool registers into; restricted to the repository."
  type        = string
  default     = "opensearch-build"
}

variable "manage_github" {
  description = "Also manage the GitHub side (runner group, ci/benchmark/release environments, role-ARN secrets). Needs a GITHUB_TOKEN with org admin."
  type        = bool
  default     = true
}

variable "release_reviewers" {
  description = "Required reviewers for the release environment."
  type = object({
    teams = optional(list(string), [])
    users = optional(list(string), [])
  })
  default = {}
}

variable "github_app" {
  description = <<-EOT
    GitHub App used by the runner control plane. Values are read from SSM SecureString parameters
    created out-of-band (see README); only the parameter names live in Terraform.
  EOT
  type = object({
    id_ssm_parameter_name             = string
    key_base64_ssm_parameter_name     = string
    webhook_secret_ssm_parameter_name = string
  })
  default = {
    id_ssm_parameter_name             = "/gha-runners/opensearch-build/github-app/id"
    key_base64_ssm_parameter_name     = "/gha-runners/opensearch-build/github-app/key-base64"
    webhook_secret_ssm_parameter_name = "/gha-runners/opensearch-build/github-app/webhook-secret"
  }
}

# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

variable "vpc_id" {
  description = "VPC for the build/test runner pools (the Jenkins agent VPC)."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for the build/test runner pools."
  type        = list(string)
}

variable "benchmark_vpc_id" {
  description = "VPC for pools with `network: benchmark` in runner-pools.yaml. null = vpc_id."
  type        = string
  default     = null
}

variable "benchmark_subnet_ids" {
  description = "Subnets for pools with `network: benchmark`. Empty = subnet_ids."
  type        = list(string)
  default     = []
}

variable "runner_ami_ssm_parameters" {
  description = <<-EOT
    SSM parameter (name) holding the AMI ID for each pool in runner-pools.yaml. Production points at
    the parameters the packer-build workflow publishes for the Jenkins AMIs; sandbox.tfvars points at
    AWS public AMI parameters. Keys must match runner-pools.yaml.
  EOT
  type        = map(string)
  default = {
    "al2023-x64-m5-4xlarge-docker-host"           = "/gha-runners/opensearch-build/ami/al2023-x64"
    "al2023-arm64-m6g-4xlarge-docker-host"        = "/gha-runners/opensearch-build/ami/al2023-arm64"
    "windows2019-x64-m5-4xlarge-docker-host"      = "/gha-runners/opensearch-build/ami/windows2019-x64"
    "windows2019-x64-m5-4xlarge-docker-builder"   = "/gha-runners/opensearch-build/ami/windows2019-x64"
    "ubuntu2404-x64-m5-2xlarge-docker-builder"    = "/gha-runners/opensearch-build/ami/ubuntu2404-x64"
    "al2023-x64-c5-4xlarge-single-host"           = "/gha-runners/opensearch-build/ami/al2023-x64"
    "al2023-arm64-c6g-4xlarge-single-host"        = "/gha-runners/opensearch-build/ami/al2023-arm64"
    "al2023-x64-m5-2xlarge-benchmark-test"        = "/gha-runners/opensearch-build/ami/al2023-x64"
    "al2023-x64-m5-2xlarge-vector-benchmark-test" = "/gha-runners/opensearch-build/ami/al2023-x64"
    "macos14-x64-mac1"                            = "/gha-runners/opensearch-build/ami/macos14-x64"
    "macos14-arm64-mac2"                          = "/gha-runners/opensearch-build/ami/macos14-arm64"
  }
}

variable "runner_max_count_overrides" {
  description = "Per-pool override of `max_runners` from runner-pools.yaml (e.g. 0 to disable a pool)."
  type        = map(number)
  default     = {}
}

variable "mac_dedicated_host_azs" {
  description = "Availability zones to allocate one EC2 Mac dedicated host in, per macOS pool (key = pool name). Pools not listed get [\"<aws_region>a\"]; an empty list = no hosts (pool cannot launch)."
  type        = map(list(string))
  default     = {}
}

variable "runner_lambda_zip_dir" {
  description = "Directory holding webhook.zip, runners.zip, runner-binaries-syncer.zip and termination-watcher.zip from the module release (scripts/download-lambdas.sh)."
  type        = string
  default     = "lambdas"
}

variable "log_retention_days" {
  description = "CloudWatch log retention for runner and control-plane logs."
  type        = number
  default     = 30
}

# ---------------------------------------------------------------------------
# Resources the OIDC roles are allowed to touch (ADR 0001 role table)
# ---------------------------------------------------------------------------

variable "metrics_opensearch_domain_arn" {
  description = "ARN of the metrics OpenSearch domain (AWS_ROLE_METRICS)."
  type        = string
}

variable "artifacts_bucket_name" {
  description = "Bucket holding CI bundles and test results (AWS_ROLE_ARTIFACTS, AWS_ROLE_TEST_RESULTS)."
  type        = string
}

variable "production_bucket_name" {
  description = "Production artifacts bucket promoted into (AWS_ROLE_PROMOTION)."
  type        = string
}

variable "production_cloudfront_distribution_arn" {
  description = "CloudFront distribution in front of the production bucket (AWS_ROLE_PROMOTION invalidations)."
  type        = string
}

variable "ecr_public_repository_arns" {
  description = "ECR Public repositories the promotion role may push to."
  type        = list(string)
  default     = []
}

variable "rpm_signing_secret_arns" {
  description = "Secrets Manager ARNs (RPM signing key/passphrase) readable by AWS_ROLE_RPM_SIGNING."
  type        = list(string)
}

variable "signer_role_arns" {
  description = "Signer roles (PGP/Windows/macOS) AWS_ROLE_SIGNING_BASE may chain into."
  type        = list(string)
}

variable "perf_cdk_role_arn_pattern" {
  description = "CDK bootstrap roles in the perf account AWS_ROLE_BENCHMARK may assume to deploy benchmark clusters."
  type        = string
}
